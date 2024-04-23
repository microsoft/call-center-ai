from azure.communication.sms import SmsSendResult
from azure.communication.sms.aio import SmsClient
from azure.core.exceptions import ClientAuthenticationError, HttpResponseError
from contextlib import asynccontextmanager
from helpers.config_models.communication_service import CommunicationServiceModel
from helpers.logging import build_logger
from helpers.pydantic_types.phone_numbers import PhoneNumber
from models.readiness import ReadinessStatus
from persistence.isms import ISms
from typing import AsyncGenerator


_logger = build_logger(__name__)


class CommunicationServicesSms(ISms):
    _config: CommunicationServiceModel

    def __init__(self, config: CommunicationServiceModel):
        _logger.info(f"Using Communication Services from number {config.phone_number}")
        self._config = config

    async def areadiness(self) -> ReadinessStatus:
        """
        Check the readiness of the Communication Services SMS service.
        """
        # TODO: How to check the readiness of the SMS service? We could send a SMS for each test, but that would be damm expensive.
        return ReadinessStatus.OK

    async def asend(self, content: str, phone_number: PhoneNumber) -> bool:
        _logger.info(f"Sending SMS to {phone_number}")
        success = False
        _logger.info(f"SMS content: {content}")
        try:
            async with self._use_client() as client:
                responses: list[SmsSendResult] = await client.send(
                    from_=str(self._config.phone_number),
                    message=content,
                    to=str(phone_number),
                )
                response = responses[0]
                if response.successful:
                    _logger.debug(f"SMS sent {response.message_id} to {response.to}")
                    success = True
                else:
                    _logger.warning(
                        f"Failed SMS to {response.to}, status {response.http_status_code}, error {response.error_message}"
                    )
        except ClientAuthenticationError:
            _logger.error(
                "Authentication error for SMS, check the credentials", exc_info=True
            )
        except HttpResponseError as e:
            _logger.error(f"Error sending SMS: {e}")
        except Exception:
            _logger.warning(f"Failed SMS to {phone_number}", exc_info=True)
        return success

    @asynccontextmanager
    async def _use_client(self) -> AsyncGenerator[SmsClient, None]:
        client = SmsClient(
            credential=self._config.access_key.get_secret_value(),
            endpoint=self._config.endpoint,
        )

        try:
            yield client
        finally:
            await client.close()
