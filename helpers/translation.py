from azure.ai.translation.text.aio import TextTranslationClient
from azure.ai.translation.text.models import TranslatedTextItem
from azure.core.credentials import AzureKeyCredential
from azure.core.exceptions import HttpResponseError
from helpers.http import azure_transport
from helpers.config import CONFIG
from helpers.logging import logger
from typing import Optional
from tenacity import (
    retry_if_exception_type,
    retry,
    stop_after_attempt,
    wait_random_exponential,
)


logger.info(f"Using Translation {CONFIG.ai_translation.endpoint}")

_cache = CONFIG.cache.instance()
_client = Optional[TextTranslationClient]


@retry(
    reraise=True,
    retry=retry_if_exception_type(HttpResponseError),
    stop=stop_after_attempt(3),
    wait=wait_random_exponential(multiplier=0.8, max=8),
)
async def translate_text(
    text: str, source_lang: str, target_lang: str
) -> Optional[str]:
    """
    Translate text from source language to target language.

    Catch errors for a maximum of 3 times.
    """
    if source_lang == target_lang:  # No need to translate
        return text

    # Try cache
    cache_key = f"{__name__}-translate_text-{text}-{source_lang}-{target_lang}"
    cached = await _cache.aget(cache_key)
    if cached:
        return cached.decode()

    # Try live
    translation: Optional[str] = None
    client = await _use_client()
    res: list[TranslatedTextItem] = await client.translate(
        body=[text],
        from_language=source_lang,
        to_language=[target_lang],
    )
    translation = res[0].translations[0].text if res and res[0].translations else None

    # Update cache
    await _cache.aset(cache_key, translation)

    return translation


async def _use_client() -> TextTranslationClient:
    """
    Generate the Translation client and close it after use.
    """
    global _client
    if not isinstance(_client, TextTranslationClient):
        _client = TextTranslationClient(
            # Performance
            transport=await azure_transport(),
            # Deployment
            endpoint=CONFIG.ai_translation.endpoint,
            # Authentication
            credential=AzureKeyCredential(
                CONFIG.ai_translation.access_key.get_secret_value()
            ),
        )
    return _client
