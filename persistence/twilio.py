from helpers.config_models.sms import TwilioModel
from helpers.logging import logger
from helpers.pydantic_types.phone_numbers import PhoneNumber
from models.readiness import ReadinessStatus
from persistence.isms import ISms
from twilio.base.exceptions import TwilioRestException
from twilio.rest import Client


class TwilioSms(ISms):
    _client: Client
    _config: TwilioModel

    def __init__(self, config: TwilioModel):
        logger.info(f"Using Twilio from number {config.phone_number}")
        self._config = config
        self._client = Client(
            password=config.auth_token.get_secret_value(),
            username=config.account_sid,
        )  # TODO: Use async client, but get multiple "attached to a different loop" errors with AsyncTwilioHttpClient

    async def areadiness(self) -> ReadinessStatus:
        """
        Check the readiness of the Twilio SMS service.

        This only check if the Twilio API is reachable and the account has remaining balance.
        """
        account_sid = self._config.account_sid
        try:
            account = self._client.api.accounts(account_sid).fetch()
            balance = account.balance.fetch()
            assert balance.balance and float(balance.balance) > 0
            return ReadinessStatus.OK
        except AssertionError:
            logger.error("Readiness test failed", exc_info=True)
        return ReadinessStatus.FAIL

    async def asend(self, content: str, phone_number: PhoneNumber) -> bool:
        logger.info(f"Sending SMS to {phone_number}")
        success = False
        logger.info(f"SMS content: {content}")
        try:
            res = self._client.messages.create(
                body=content,
                from_=str(self._config.phone_number),
                to=phone_number,
            )
            # TODO: How to check the delivery status? Seems present in "res.status" but not documented
            if res.error_message:
                logger.warning(
                    f"Failed SMS to {phone_number}, status {res.error_code}, error {res.error_message}"
                )
            else:
                logger.debug(f"SMS sent to {phone_number}")
                success = True
        except TwilioRestException as e:
            logger.error(f"Error sending SMS: {e}")
        except Exception:
            logger.warning(f"Failed SMS to {phone_number}", exc_info=True)
        return success
