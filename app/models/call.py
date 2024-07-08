from datetime import datetime, UTC, tzinfo
from helpers.config_models.conversation import LanguageEntryModel
from helpers.config_models.conversation import WorkflowInitiateModel
from helpers.pydantic_types.phone_numbers import PhoneNumber
from models.message import MessageModel, ActionEnum as MessageActionEnum
from models.next import NextModel
from models.reminder import ReminderModel
from models.synthesis import SynthesisModel
from models.training import TrainingModel
from pydantic import BaseModel, Field, computed_field, field_validator, ValidationInfo
from typing import Any, Optional
from uuid import UUID, uuid4
import asyncio
import random
import string


class CallInitiateModel(WorkflowInitiateModel):
    phone_number: PhoneNumber


class CallGetModel(BaseModel):
    # Immutable fields
    call_id: UUID = Field(default_factory=uuid4, frozen=True)
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC), frozen=True)
    # Editable fields
    initiate: CallInitiateModel = Field(frozen=True)
    claim: dict[str, Any] = (
        {}
    )  # Place after "initiate" as it depends on it for validation
    messages: list[MessageModel] = []
    next: Optional[NextModel] = None
    reminders: list[ReminderModel] = []
    synthesis: Optional[SynthesisModel] = None
    voice_id: Optional[str] = None

    @computed_field
    @property
    def in_progress(self) -> bool:
        """
        Check if the call is in progress.

        The call is in progress if the most recent message action status (CALL or HANGUP) is CALL. Otherwise, it is not in progress.
        """
        # Reverse
        inverted_messages = self.messages.copy()
        inverted_messages.reverse()
        # Search for the first action we want
        for message in inverted_messages:
            if message.action == MessageActionEnum.CALL:
                return True
            elif message.action == MessageActionEnum.HANGUP:
                return False
        # Otherwise, we assume the call is completed
        return False

    @field_validator("claim")
    def _validate_claim(
        cls, claim: Optional[dict[str, Any]], info: ValidationInfo
    ) -> dict[str, Any]:
        initiate: Optional[CallInitiateModel] = info.data.get("initiate", None)
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


class CallStateModel(CallGetModel, extra="ignore"):
    # Immutable fields
    callback_secret: str = Field(
        default="".join(
            random.choice(string.ascii_letters + string.digits) for _ in range(16)
        ),
        frozen=True,
    )
    # Editable fields
    lang_short_code: Optional[str] = None
    recognition_retry: int = 0

    @computed_field
    @property
    def lang(self) -> LanguageEntryModel:  # type: ignore
        from helpers.config import CONFIG

        lang = CONFIG.conversation.initiate.lang
        default = lang.default_lang
        if self.lang_short_code:
            return next(
                (
                    lang
                    for lang in lang.availables
                    if lang.short_code == self.lang_short_code
                ),
                default,
            )
        return default

    @lang.setter
    def lang(self, short_code: str) -> None:
        self.lang_short_code = short_code

    async def trainings(self, cache_only: bool = True) -> list[TrainingModel]:
        """
        Get the trainings from the last messages.

        Is using query expansion from last messages. Then, data is sorted by score.
        """
        from helpers.config import CONFIG
        from helpers.logging import tracer

        with tracer.start_as_current_span("trainings"):
            search = CONFIG.ai_search.instance()
            tasks = await asyncio.gather(
                *[
                    search.training_asearch_all(
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
        return PhoneNumber.tz(self.initiate.phone_number)
