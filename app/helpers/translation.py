from azure.ai.translation.text.aio import TextTranslationClient
from azure.ai.translation.text.models import TranslatedTextItem
from azure.core.credentials import AzureKeyCredential
from azure.core.exceptions import HttpResponseError
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_random_exponential,
)

from app.helpers.cache import lru_acache
from app.helpers.config import CONFIG
from app.helpers.http import azure_transport
from app.helpers.logging import logger

logger.info("Using Translation %s", CONFIG.ai_translation.endpoint)

_cache = CONFIG.cache.instance


@retry(
    reraise=True,
    retry=retry_if_exception_type(HttpResponseError),
    stop=stop_after_attempt(3),
    wait=wait_random_exponential(multiplier=0.8, max=8),
)
async def translate_text(text: str, source_lang: str, target_lang: str) -> str | None:
    """
    Translate text from source language to target language.

    If the source and target languages are the same, the original text is returned. Catch errors for a maximum of 3 times.
    """
    # No need to translate
    if source_lang == target_lang:
        return text

    # Try cache
    cache_key = f"{__name__}-translate_text-{text}-{source_lang}-{target_lang}"
    cached = await _cache.get(cache_key)
    if cached:
        return cached.decode()

    # Try live
    translation: str | None = None
    async with await _use_client() as client:
        res: list[TranslatedTextItem] = await client.translate(
            body=[text],
            from_language=source_lang,
            to_language=[target_lang],
        )
    translation = res[0].translations[0].text if res and res[0].translations else None

    # Update cache
    await _cache.set(
        key=cache_key,
        ttl_sec=60 * 60 * 24,  # 1 day
        value=translation,
    )

    return translation


@lru_acache()
async def _use_client() -> TextTranslationClient:
    """
    Generate the Translation client and close it after use.
    """
    logger.debug("Using Translation client for %s", CONFIG.ai_translation.endpoint)

    return TextTranslationClient(
        # Performance
        transport=await azure_transport(),
        # Deployment
        endpoint=CONFIG.ai_translation.endpoint,
        # Authentication
        credential=AzureKeyCredential(
            CONFIG.ai_translation.access_key.get_secret_value()
        ),
    )
