from datetime import datetime
from pydantic import BaseModel, Field, field_validator
from pydantic_extra_types.phone_numbers import PhoneNumber
from typing import Optional, Set
import random
import re
import string


# E164 is standard accross all Microsoft services
PhoneNumber.phone_format = "E164"

EMAIL_PARSER_R = r"[\w.]+@[\w.]+\.[\w]+"


class ClaimModel(BaseModel):
    # Immutable fields
    claim_id: str = Field(
        default_factory=(
            lambda: "".join(
                random.choice(string.ascii_lowercase + string.digits) for _ in range(6)
            )
        ),
        frozen=True,
    )
    created_at: datetime = Field(default_factory=datetime.utcnow, frozen=True)
    # Editable fields
    extra_details: Optional[str] = None
    incident_date_time: Optional[str] = None
    incident_description: Optional[str] = None
    incident_location: Optional[str] = None
    injuries_description: Optional[str] = None
    insurance_type: Optional[str] = None
    involved_parties: Optional[str] = None
    medical_records: Optional[str] = None
    police_report_number: Optional[str] = None
    policy_number: Optional[str] = None
    policyholder_email: Optional[str] = None
    policyholder_name: Optional[str] = None
    policyholder_phone: Optional[PhoneNumber] = None
    pre_existing_damage_description: Optional[str] = None
    property_damage_description: Optional[str] = None
    repair_replacement_estimates: Optional[str] = None
    stolen_lost_items: Optional[str] = None
    vehicle_info: Optional[str] = None
    witnesses: Optional[str] = None

    @field_validator("policyholder_email")
    @classmethod
    def _policyholder_email_validator(cls, email: str) -> str:
        if not re.match(EMAIL_PARSER_R, email):
            raise ValueError(
                'Invalid email address, please use a valid email (e.g. "my.name@domain.com").'
            )
        return email

    @staticmethod
    def editable_fields() -> Set[str]:
        return ClaimModel.model_json_schema()["properties"].keys() - [
            "id",
            "created_at",
        ]
