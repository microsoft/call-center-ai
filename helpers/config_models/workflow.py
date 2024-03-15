from functools import cached_property
from pydantic import BaseModel
from helpers.pydantic_types.phone_numbers import PhoneNumber


class LanguageEntryModel(BaseModel):
    """
    Language entry, containing the standard short code, an human name and the Azure Text-to-Speech voice name.

    See: https://learn.microsoft.com/en-us/azure/ai-services/speech-service/language-support?tabs=tts#supported-languages
    """

    pronunciations_en: list[str]
    short_code: str
    voice: str

    @property
    def human_name(self) -> str:
        return self.pronunciations_en[0]

    def __str__(self):  # Pretty print for logs
        return self.short_code


class LanguageModel(BaseModel):
    """
    Manage language for the workflow.
    """

    default_short_code: str = "fr-FR"
    # Voice list from Azure TTS
    # See: https://learn.microsoft.com/en-us/azure/ai-services/speech-service/language-support?tabs=tts
    availables: list[LanguageEntryModel] = [
        LanguageEntryModel(
            pronunciations_en=["French", "FR", "France"],
            short_code="fr-FR",
            # Use voice optimized for conversational use
            # See: https://techcommunity.microsoft.com/t5/ai-azure-ai-services-blog/introducing-more-multilingual-ai-voices-optimized-for/ba-p/4012832
            voice="fr-FR-VivienneMultilingualNeural",
        ),
        LanguageEntryModel(
            short_code="en-US",
            pronunciations_en=["English", "EN", "United States"],
            # Use voice optimized for conversational use
            # See: https://techcommunity.microsoft.com/t5/ai-azure-ai-services-blog/introducing-more-multilingual-ai-voices-optimized-for/ba-p/4012832
            voice="en-US-AvaMultilingualNeural",
        ),
        LanguageEntryModel(
            short_code="es-ES",
            pronunciations_en=["Spanish", "ES", "Spain"],
            # Use voice optimized for conversational use
            # See: https://techcommunity.microsoft.com/t5/ai-azure-ai-services-blog/introducing-7-new-realistic-ai-voices-optimized-for/ba-p/3971966
            voice="es-ES-XimenaNeural",
        ),
        LanguageEntryModel(
            short_code="zh-CN",
            pronunciations_en=["Chinese", "ZH", "China"],
            # Use voice optimized for conversational use
            # See: https://techcommunity.microsoft.com/t5/ai-azure-ai-services-blog/introducing-more-multilingual-ai-voices-optimized-for/ba-p/4012832
            voice="zh-CN-XiaoxiaoMultilingualNeural",
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


class WorkflowModel(BaseModel):
    agent_phone_number: PhoneNumber
    bot_company: str
    bot_name: str
    conversation_timeout_hour: int = 72  # 3 days
    intelligence_hard_timeout_sec: int = 180  # 3 minutes
    intelligence_soft_timeout_sec: int = 30  # 30 seconds
    lang: LanguageModel = LanguageModel()  # Object is fully defined by default
