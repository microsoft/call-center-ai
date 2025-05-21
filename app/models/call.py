import asyncio
import random
import string
from datetime import UTC, datetime, tzinfo
from typing import Any
from uuid import UUID, uuid4

from pydantic import BaseModel, Field, ValidationInfo, field_validator

from app.helpers.config_models.conversation import (
    LanguageEntryModel,
    WorkflowInitiateModel,
)
from app.helpers.monitoring import tracer
from app.helpers.pydantic_types.phone_numbers import PhoneNumber
from app.models.message import (
    ActionEnum as MessageActionEnum,
    MessageModel,
    PersonaEnum as MessagePersonaEnum,
    StyleEnum as MessageStyleEnum,
)
from app.models.next import NextModel
from app.models.reminder import ReminderModel
from app.models.synthesis import SynthesisModel
from app.models.training import TrainingModel


class CallInitiateModel(WorkflowInitiateModel):
    phone_number: PhoneNumber


class CallGetModel(BaseModel):
    # Immutable fields
    call_id: UUID = Field(default_factory=uuid4, frozen=True)
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC), frozen=True)
    # Editable fields
    in_progress: bool = False
    initiate: CallInitiateModel = Field(frozen=True)
    claim: dict[
        str, Any
    ] = {}  # Place after "initiate" as it depends on it for validation
    messages: list[MessageModel] = []
    next: NextModel | None = None
    reminders: list[ReminderModel] = []
    synthesis: SynthesisModel | None = None

    @field_validator("claim")
    @classmethod
    def _validate_claim(
        cls, claim: dict[str, Any] | None, info: ValidationInfo
    ) -> dict[str, Any]:
        """
        Validate the claim field against the initiate data model.
        """
        initiate: CallInitiateModel | None = info.data.get("initiate", None)
        if not initiate:
            return {}
        return (
            initiate.claim_model()
            .model_validate(claim)
            .model_dump(
                exclude_none=True,
                mode="json",  # Field must be serialized as JSON in other parts of the code
            )
        )

    @field_validator("messages")
    @classmethod
    def _validate_messages(cls, messages: list[MessageModel]) -> list[MessageModel]:
        """
        Merge messages with the same persona.
        """

        # Skip if there are no messages
        if not messages:
            return messages

        # Iterate over the messages
        merged: list[MessageModel] = [messages[0]]
        for new_message in messages[1:]:
            # If the last message is not from the same persona or action, keep it as is
            last = merged[-1]
            if last.persona != new_message.persona or last.action != new_message.action:
                merged.append(new_message)
                continue

            # Merge the content and tool calls
            last.content = (last.content + " " + new_message.content).strip()
            last.tool_calls = list({*last.tool_calls, *new_message.tool_calls})
            # Override the style
            last.style = new_message.style

        return merged


class CallStateModel(CallGetModel, extra="ignore"):
    # Immutable fields
    callback_secret: str = Field(
        default="".join(
            random.choice(string.ascii_letters + string.digits) for _ in range(16)
        ),
        frozen=True,
    )
    # Editable fields
    lang_short_code: str | None = None
    last_interaction_at: datetime | None = None
    recognition_retry: int = 0
    voice_id: str | None = None

    @property
    def lang(self) -> LanguageEntryModel:  # pyright: ignore
        default = self.initiate.lang.default_lang
        if self.lang_short_code:
            return next(
                (
                    lang
                    for lang in self.initiate.lang.availables
                    if lang.short_code == self.lang_short_code
                ),
                default,
            )
        return default

    async def trainings(self, cache_only: bool = True) -> list[TrainingModel]:
        """
        Get the trainings from the last messages.

        Is using query expansion from last messages. Then, data is sorted by score.
        """
        from app.helpers.config import CONFIG

        with tracer.start_as_current_span("call_trainings"):
            search = CONFIG.ai_search.instance
            tasks = await asyncio.gather(
                *[
                    search.training_search_all(
                        cache_only=cache_only,
                        lang=self.lang.short_code,
                        text=message.content,
                    )
                    for message in self.messages[
                        -CONFIG.ai_search.expansion_n_messages :
                    ]
                ],
            )  # Get trainings from last messages
            trainings = sorted(
                set(
                    training
                    for trainings in tasks
                    for training in trainings or []
                    if training.score >= CONFIG.ai_search.strictness
                )
            )  # Flatten, remove duplicates, sort by score, filter by strictness
            return trainings

    def tz(self) -> tzinfo:
        """
        Get the timezone of the phone number.
        """
        return PhoneNumber.tz(self.initiate.phone_number)

    def last_assistant_style(self) -> MessageStyleEnum:
        """
        Get the last assistant message style.
        """
        inverted_messages = self.messages.copy()
        inverted_messages.reverse()
        for message in inverted_messages:
            if message.persona != MessagePersonaEnum.ASSISTANT:
                continue
            return message.style
        return MessageStyleEnum.NONE

    def had_interaction(self) -> bool:
        """
        Check if the call had an interaction.

        An interaction is defined as a call with a human message.
        """
        return not (
            len(self.messages) >= 3  # noqa: PLR2004
            and self.messages[-3].action == MessageActionEnum.CALL
            and self.messages[-2].persona == MessagePersonaEnum.ASSISTANT
            and self.messages[-1].action == MessageActionEnum.HANGUP
        )
