from azure.communication.sms import SmsSendResult
from azure.communication.sms.aio import SmsClient
from azure.core.exceptions import ClientAuthenticationError, HttpResponseError
from helpers.http import azure_transport
from helpers.config_models.communication_services import CommunicationServicesModel
from helpers.logging import logger
from helpers.pydantic_types.phone_numbers import PhoneNumber
from models.readiness import ReadinessEnum
from persistence.isms import ISms
from typing import Optional


class CommunicationServicesSms(ISms):
    _client: Optional[SmsClient] = None
    _config: CommunicationServicesModel

    def __init__(self, config: CommunicationServicesModel):
        logger.info(f"Using Communication Services from number {config.phone_number}")
        self._config = config

    async def areadiness(self) -> ReadinessEnum:
        """
        Check the readiness of the Communication Services SMS service.
        """
        # TODO: How to check the readiness of the SMS service? We could send a SMS for each test, but that would be damm expensive.
        return ReadinessEnum.OK

    async def asend(self, content: str, phone_number: PhoneNumber) -> bool:
        logger.info(f"Sending SMS to {phone_number}")
        success = False
        logger.info(f"SMS content: {content}")
        try:
            async with await self._use_client() as client:
                responses: list[SmsSendResult] = await client.send(
                    from_=str(self._config.phone_number),
                    message=content,
                    to=phone_number,
                )
                response = responses[0]
                if response.successful:
                    logger.debug(f"SMS sent {response.message_id} to {response.to}")
                    success = True
                else:
                    logger.warning(
                        f"Failed SMS to {response.to}, status {response.http_status_code}, error {response.error_message}"
                    )
        except ClientAuthenticationError:
            logger.error(
                "Authentication error for SMS, check the credentials", exc_info=True
            )
        except HttpResponseError as e:
            logger.error(f"Error sending SMS: {e}")
        except Exception:
            logger.warning(f"Failed SMS to {phone_number}", exc_info=True)
        return success

    async def _use_client(self) -> SmsClient:
        if not self._client:
            self._client = SmsClient(
                # Deployment
                endpoint=self._config.endpoint,
                # Performance
                transport=await azure_transport(),
                # Authentication
                credential=self._config.access_key.get_secret_value(),
            )
        return self._client
