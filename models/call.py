from datetime import datetime, UTC, tzinfo
from helpers.config_models.workflow import LanguageEntryModel
from helpers.pydantic_types.phone_numbers import PhoneNumber
from models.claim import ClaimModel
from models.message import MessageModel, ActionEnum as MessageActionEnum
from models.next import NextModel
from models.reminder import ReminderModel
from models.synthesis import SynthesisModel
from models.training import TrainingModel
from pydantic import BaseModel, Field, computed_field
from typing import Optional
from uuid import UUID, uuid4
import asyncio
import random
import string


class CallGetModel(BaseModel):
    # Immutable fields
    call_id: UUID = Field(default_factory=uuid4, frozen=True)
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC), frozen=True)
    # Editable fields
    claim: ClaimModel = Field(default_factory=ClaimModel)
    messages: list[MessageModel] = []
    next: Optional[NextModel] = None
    phone_number: PhoneNumber
    recognition_retry: int = Field(default=0)
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

    def tz(self) -> tzinfo:
        return PhoneNumber.tz(self.phone_number)


class CallStateModel(CallGetModel):
    # Immutable fields
    callback_secret: str = Field(
        default="".join(
            random.choice(string.ascii_letters + string.digits) for _ in range(16)
        ),
        frozen=True,
    )
    # Editable fields
    lang_short_code: Optional[str] = Field(default=None)
    voice_recognition_retry: int = Field(
        default=0,
        validation_alias="recognition_retry",  # Compatibility with v1
    )

    @computed_field
    @property
    def lang(self) -> LanguageEntryModel:  # type: ignore
        from helpers.config import CONFIG

        if self.lang_short_code:
            return next(
                (
                    lang
                    for lang in CONFIG.workflow.lang.availables
                    if lang.short_code == self.lang_short_code
                ),
                CONFIG.workflow.lang.default_lang,
            )
        return CONFIG.workflow.lang.default_lang

    @lang.setter
    def lang(self, short_code: str) -> None:
        self.lang_short_code = short_code

    async def trainings(self) -> list[TrainingModel]:
        """
        Get the trainings from the last messages.

        Is using query expansion from last messages. Then, data is sorted by score.
        """
        from helpers.config import CONFIG
        from helpers.logging import TRACER

        with TRACER.start_as_current_span("trainings"):
            search = CONFIG.ai_search.instance()
            trainings_tasks = await asyncio.gather(
                *[
                    search.training_asearch_all(
                        text=message.content, lang=self.lang.short_code
                    )
                    for message in self.messages[-CONFIG.ai_search.expansion_k :]
                ],
            )  # Get trainings from last messages
            trainings = sorted(
                set(
                    training
                    for trainings in trainings_tasks
                    for training in trainings or []
                )
            )  # Flatten, remove duplicates, and sort by score
            return trainings
