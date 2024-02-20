from datetime import datetime
from helpers.config_models.workflow import LanguageEntryModel
from models.claim import ClaimModel
from models.message import MessageModel
from models.next import NextModel
from models.reminder import ReminderModel
from models.synthesis import SynthesisModel
from pydantic import BaseModel, Field, computed_field, SecretStr
from typing import List, Optional
from uuid import UUID, uuid4
import random
import string


class CallModel(BaseModel):
    # Immutable fields
    call_id: UUID = Field(default_factory=uuid4, frozen=True)
    created_at: datetime = Field(default_factory=datetime.utcnow, frozen=True)
    callback_secret: str = Field(
        default="".join(
            random.choice(string.ascii_letters + string.digits) for _ in range(16)
        ),
        frozen=True,
    )
    # Private fields
    lang_short_code: Optional[str] = None
    # Editable fields
    claim: ClaimModel = Field(default_factory=ClaimModel)
    messages: List[MessageModel] = []
    next: Optional[NextModel] = None
    phone_number: str
    recognition_retry: int = Field(default=0)
    reminders: List[ReminderModel] = []
    synthesis: Optional[SynthesisModel] = None

    @computed_field
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
    def lang(self, value: LanguageEntryModel) -> None:
        self.lang_short_code = value.short_code
