from azure.ai.translation.text.aio import TextTranslationClient
from azure.ai.translation.text.models import InputTextItem, TranslatedTextItem
from azure.core.credentials import AzureKeyCredential
from contextlib import asynccontextmanager
from helpers.config import CONFIG
from helpers.logging import build_logger
from typing import AsyncGenerator, Optional
from azure.core.exceptions import HttpResponseError
from tenacity import (
    retry,
    stop_after_attempt,
    wait_random_exponential,
    retry_if_exception_type,
)


_logger = build_logger(__name__)
_logger.info(f"Using Translation {CONFIG.ai_translation.endpoint}")

_cache = CONFIG.cache.instance()


@retry(
    reraise=True,
    retry=retry_if_exception_type(HttpResponseError),
    stop=stop_after_attempt(3),
    wait=wait_random_exponential(multiplier=0.5, max=30),
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
    async with _use_client() as client:  # Perform translation
        res: list[TranslatedTextItem] = await client.translate(
            content=[InputTextItem(text=text)],  # type: ignore
            from_parameter=source_lang,
            to=[target_lang],
        )
        translation = (
            res[0].translations[0].text if res and res[0].translations else None
        )

    # Update cache
    await _cache.aset(cache_key, translation)

    return translation


@asynccontextmanager
async def _use_client() -> AsyncGenerator[TextTranslationClient, None]:
    client = TextTranslationClient(
        credential=AzureKeyCredential(
            CONFIG.ai_translation.access_key.get_secret_value()
        ),
        endpoint=CONFIG.ai_translation.endpoint,
    )
    try:
        yield client
    finally:
        await client.close()
