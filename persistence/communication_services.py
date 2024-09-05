from typing import Optional

from azure.communication.sms import SmsSendResult
from azure.communication.sms.aio import SmsClient
from azure.core.exceptions import ClientAuthenticationError, HttpResponseError

from helpers.config_models.communication_services import CommunicationServicesModel
from helpers.http import azure_transport
from helpers.logging import logger
from helpers.pydantic_types.phone_numbers import PhoneNumber
from models.readiness import ReadinessEnum
from persistence.isms import ISms


class CommunicationServicesSms(ISms):
    _client: SmsClient | None = None
    _config: CommunicationServicesModel

    def __init__(self, config: CommunicationServicesModel):
        logger.info("Using Communication Services from number %s", config.phone_number)
        self._config = config

    async def areadiness(self) -> ReadinessEnum:
        """
        Check the readiness of the Communication Services SMS service.
        """
        # TODO: How to check the readiness of the SMS service? We could send a SMS for each test, but that would be damm expensive.
        return ReadinessEnum.OK

    async def asend(self, content: str, phone_number: PhoneNumber) -> bool:
        logger.info("Sending SMS to %s", phone_number)
        success = False
        logger.info("SMS content: %s", content)
        try:
            async with await self._use_client() as client:
                responses: list[SmsSendResult] = await client.send(
                    from_=str(self._config.phone_number),
                    message=content,
                    to=phone_number,
                )
                response = responses[0]
                if response.successful:
                    logger.debug("SMS sent %s to %s", response.message_id, response.to)
                    success = True
                else:
                    logger.warning(
                        "Failed SMS to %s, status %s, error %s",
                        response.to,
                        response.http_status_code,
                        response.error_message,
                    )
        except ClientAuthenticationError:
            logger.error(
                "Authentication error for SMS, check the credentials", exc_info=True
            )
        except HttpResponseError:
            logger.error("Error sending SMS to %s", phone_number, exc_info=True)
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
