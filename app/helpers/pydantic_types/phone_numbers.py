from datetime import tzinfo

import phonenumbers
from pydantic_extra_types.phone_numbers import PhoneNumber as PydanticPhoneNumber
from pytz import country_timezones, timezone, utc

from app.helpers.cache import lru_cache


class PhoneNumber(PydanticPhoneNumber):
    phone_format = "E164"  # E164 is standard accross all Microsoft services

    @lru_cache()  # Cache results in memory as func is executed many times on the same content
    def tz(self: PydanticPhoneNumber) -> tzinfo:
        """
        Return timezone of a phone number.

        If the country code cannot be determined, return UTC as a fallback.
        """
        phone = phonenumbers.parse(self)
        if not phone.country_code:
            return utc
        region_code = phonenumbers.region_code_for_country_code(phone.country_code)
        tz_name = country_timezones[region_code][0]
        return timezone(tz_name)
