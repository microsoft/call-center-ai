from functools import cached_property
from pydantic_extra_types.phone_numbers import PhoneNumber
from pydantic_settings import BaseSettings
from typing import List


# E164 is standard accross all Microsoft services
PhoneNumber.phone_format = "E164"


class LanguageEntryModel(BaseSettings):
    """
    Language entry, containing the standard short code, an human name and the Azure Text-to-Speech voice name.

    See: https://learn.microsoft.com/en-us/azure/ai-services/speech-service/language-support?tabs=tts#supported-languages
    """

    pronunciations_en: List[str]
    short_code: str
    voice: str

    @property
    def human_name(self) -> str:
        return self.pronunciations_en[0]

    def __str__(self):  # Pretty print for logs
        return self.short_code


class LanguageModel(BaseSettings):
    """
    Manage language for the workflow.
    """

    default_short_code: str = "fr-FR"
    availables: List[LanguageEntryModel] = [
        LanguageEntryModel(
            pronunciations_en=["French", "FR", "France"],
            short_code="fr-FR",
            voice="fr-FR-DeniseNeural",
        ),
        LanguageEntryModel(
            short_code="en-US",
            pronunciations_en=["English", "EN", "United States"],
            voice="en-US-AvaNeural",
        ),
        LanguageEntryModel(
            short_code="es-ES",
            pronunciations_en=["Spanish", "ES", "Spain"],
            voice="es-ES-ElviraNeural",
        ),
        LanguageEntryModel(
            short_code="zh-CN",
            pronunciations_en=["Chinese", "ZH", "China"],
            voice="zh-CN-XiaoxiaoNeural",
        ),
    ]

    @cached_property
    def default_lang(self) -> LanguageEntryModel:
        return next(
            (
                lang
                for lang in self.availables
                if self.default_short_code == lang.short_code
            ),
            self.availables[0],
        )


class WorkflowModel(BaseSettings):
    agent_phone_number: PhoneNumber
    bot_company: str
    bot_name: str
    conversation_timeout_hour: int = 72  # 3 days
    intelligence_hard_timeout_sec: int = 180  # 3 minutes
    intelligence_soft_timeout_sec: int = 30  # 30 seconds
    lang: LanguageModel = LanguageModel()  # Object is fully defined by default
