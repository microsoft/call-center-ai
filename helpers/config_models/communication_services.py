from pydantic import BaseModel, SecretStr

from helpers.pydantic_types.phone_numbers import PhoneNumber


class CommunicationServicesModel(BaseModel):
    access_key: SecretStr
    call_queue_name: str
    endpoint: str
    phone_number: PhoneNumber
    post_queue_name: str
    recording_container_url: str
    resource_id: str
    sms_queue_name: str
    trainings_queue_name: str
