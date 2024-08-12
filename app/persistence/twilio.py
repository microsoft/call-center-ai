from typing import Optional

from twilio.base.exceptions import TwilioRestException
from twilio.rest import Client

from helpers.config_models.sms import TwilioModel
from helpers.http import twilio_http
from helpers.logging import logger
from helpers.pydantic_types.phone_numbers import PhoneNumber
from models.readiness import ReadinessEnum
from persistence.isms import ISms


class TwilioSms(ISms):
    _client: Optional[Client] = None
    _config: TwilioModel

    def __init__(self, config: TwilioModel):
        logger.info("Using Twilio from number %s", config.phone_number)
        self._config = config

    async def areadiness(self) -> ReadinessEnum:
        """
        Check the readiness of the Twilio SMS service.

        This only check if the Twilio API is reachable and the account has remaining balance.
        """
        account_sid = self._config.account_sid
        client = await self._use_client()
        try:
            account = await client.api.accounts(account_sid).fetch_async()
            balance = await account.balance.fetch_async()
            assert balance.balance and float(balance.balance) > 0
            return ReadinessEnum.OK
        except AssertionError:
            logger.error("Readiness test failed", exc_info=True)
        except Exception:  # pylint: disable=broad-exception-caught
            logger.error("Unknown error while checking Twilio readiness", exc_info=True)
        return ReadinessEnum.FAIL

    async def asend(self, content: str, phone_number: PhoneNumber) -> bool:
        logger.info("Sending SMS to %s", phone_number)
        success = False
        logger.info("SMS content: %s", content)
        client = await self._use_client()
        try:
            res = await client.messages.create_async(
                body=content,
                from_=str(self._config.phone_number),
                to=phone_number,
            )
            # TODO: How to check the delivery status? Seems present in "res.status" but not documented
            if res.error_message:
                logger.warning(
                    "Failed SMS to %s, status %s, error %s",
                    phone_number,
                    res.error_code,
                    res.error_message,
                )
            else:
                logger.debug("SMS sent to %s", phone_number)
                success = True
        except TwilioRestException:
            logger.error("Error sending SMS to %s", phone_number, exc_info=True)
        return success

    async def _use_client(self) -> Client:
        if not self._client:
            self._client = Client(
                # Performance
                http_client=await twilio_http(),
                # Authentication
                password=self._config.auth_token.get_secret_value(),
                username=self._config.account_sid,
            )
        return self._client
