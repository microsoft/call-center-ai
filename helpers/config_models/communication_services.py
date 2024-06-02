from helpers.pydantic_types.phone_numbers import PhoneNumber
from pydantic import SecretStr, BaseModel, computed_field


class CommunicationServicesModel(BaseModel):
    access_key: SecretStr
    call_queue_name: str
    endpoint: str
    phone_number: PhoneNumber
    post_queue_name: str
    sms_queue_name: str
    trainings_queue_name: str
