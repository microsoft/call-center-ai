from datetime import datetime
from typing import Annotated, Any

from pydantic import BaseModel, ConfigDict, EmailStr, Field, create_model
from pydantic.fields import FieldInfo

from app.helpers.pydantic_types.phone_numbers import PhoneNumber
from app.models.claim import ClaimFieldModel, ClaimTypeEnum


class LanguageEntryModel(BaseModel):
    """
    Language entry, containing the standard short code, an human name and the Azure Text-to-Speech voice name.

    See: https://learn.microsoft.com/en-us/azure/ai-services/speech-service/language-support?tabs=tts#supported-languages
    """

    custom_voice_endpoint_id: str | None = None
    pronunciations_en: list[str]
    short_code: str
    voice: str

    @property
    def human_name(self) -> str:
        return self.pronunciations_en[0]

    def __str__(self):
        """
        Return the short code as string.
        """
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
            voice="fr-FR-VivienneMultilingualNeural",
        ),
        LanguageEntryModel(
            pronunciations_en=["English", "EN", "United States"],
            short_code="en-US",
            voice="en-US-ShimmerTurboMultilingualNeural",
        ),
        LanguageEntryModel(
            pronunciations_en=["Spanish", "ES", "Spain"],
            short_code="es-ES",
            voice="es-ES-ArabellaMultilingualNeural",
        ),
        LanguageEntryModel(
            pronunciations_en=["Chinese", "ZH", "China"],
            short_code="zh-CN",
            voice="zh-CN-XiaoyuMultilingualNeural",
        ),
    ]

    @property
    def default_lang(self) -> LanguageEntryModel:
        return next(
            (
                lang
                for lang in self.availables
                if self.default_short_code == lang.short_code
            ),
            self.availables[0],
        )


class WorkflowInitiateModel(BaseModel):
    agent_phone_number: PhoneNumber
    bot_company: str
    bot_name: str
    claim: list[ClaimFieldModel] = [
        ClaimFieldModel(
            description="Date and time of the incident",
            name="incident_datetime",
            type=ClaimTypeEnum.DATETIME,
        ),
        ClaimFieldModel(
            description="Description of the incident",
            name="incident_description",
            type=ClaimTypeEnum.TEXT,
        ),
        ClaimFieldModel(
            description="Location of the incident",
            name="incident_location",
            type=ClaimTypeEnum.TEXT,
        ),
        ClaimFieldModel(
            description="Injuries sustained during the incident",
            name="injuries",
            type=ClaimTypeEnum.TEXT,
        ),
        ClaimFieldModel(
            description="Involved parties in the incident",
            name="involved_parties",
            type=ClaimTypeEnum.TEXT,
        ),
        ClaimFieldModel(
            description="Medical records related to the incident",
            name="medical_records",
            type=ClaimTypeEnum.TEXT,
        ),
        ClaimFieldModel(
            description="Policy number of the customer",
            name="policy_number",
            type=ClaimTypeEnum.TEXT,
        ),
        ClaimFieldModel(
            description="Pre-existing damages",
            name="pre_existing_damages",
            type=ClaimTypeEnum.TEXT,
        ),
        ClaimFieldModel(
            description="Witnesses of the incident",
            name="witnesses",
            type=ClaimTypeEnum.TEXT,
        ),
    ]  # Configured like in v4 for compatibility
    lang: LanguageModel = LanguageModel()  # Object is fully defined by default
    prosody_rate: float = Field(
        default=1.0,
        ge=0.75,
        le=1.25,
    )
    task: str = "Helping the customer to file an insurance claim. The customer is probably calling because they have a problem with something covered by their policy, but it's not certain. The assistant needs information from the customer to complete the claim. The conversation is over when all the data relevant to the case has been collected. Filling in as much information as possible is important for further processing."

    def claim_model(self) -> type[BaseModel]:
        return _fields_to_pydantic(
            name="ClaimEntryModel",
            fields=[
                *self.claim,
                ClaimFieldModel(
                    description="Email of the customer",
                    name="policyholder_email",
                    type=ClaimTypeEnum.EMAIL,
                ),
                ClaimFieldModel(
                    description="First and last name of the customer",
                    name="policyholder_name",
                    type=ClaimTypeEnum.TEXT,
                ),
                ClaimFieldModel(
                    description="Phone number of the customer",
                    name="policyholder_phone",
                    type=ClaimTypeEnum.PHONE_NUMBER,
                ),
            ],
        )


class ConversationModel(BaseModel):
    # TODO: This could be simplified by removing the parent class but would cause a breaking change
    initiate: WorkflowInitiateModel


def _fields_to_pydantic(name: str, fields: list[ClaimFieldModel]) -> type[BaseModel]:
    field_definitions = {field.name: _field_to_pydantic(field) for field in fields}
    return create_model(
        name,
        **field_definitions,  # pyright: ignore
        __config__=ConfigDict(
            extra="ignore",  # Avoid validation errors, just ignore data
        ),
    )


def _field_to_pydantic(
    field: ClaimFieldModel,
) -> Annotated[Any, ...] | tuple[type, FieldInfo]:
    field_type = _type_to_pydantic(field.type)
    return (
        field_type | None,
        Field(
            default=None,
            description=field.description,
        ),
    )


def _type_to_pydantic(
    data: ClaimTypeEnum,
) -> type | Annotated[Any, ...]:
    match data:
        case ClaimTypeEnum.DATETIME:
            return datetime
        case ClaimTypeEnum.EMAIL:
            return EmailStr
        case ClaimTypeEnum.PHONE_NUMBER:
            return PhoneNumber
        case ClaimTypeEnum.TEXT:
            return str
