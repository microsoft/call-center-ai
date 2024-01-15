from pydantic import BaseModel
from typing import Optional


class ClaimModel(BaseModel):
    additional_documentation: Optional[str] = None
    claim_explanation: Optional[str] = None
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
    policyholder_contact_info: Optional[str] = None
    policyholder_name: Optional[str] = None
    pre_existing_damage_description: Optional[str] = None
    property_damage_description: Optional[str] = None
    repair_replacement_estimates: Optional[str] = None
    stolen_lost_items: Optional[str] = None
    vehicle_info: Optional[str] = None
    witnesses: Optional[str] = None
