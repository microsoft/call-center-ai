# Mock environment variables
from os import environ


environ["PUBLIC_DOMAIN"] = "dummy"


# General imports
from helpers.config import CONFIG
from helpers.logging import logger
from models.call import CallStateModel, CallInitiateModel
from tests.conftest import CallAutomationClientMock
from helpers.call_events import (
    on_call_connected,
    on_call_disconnected,
    on_end_call,
    on_ivr_recognized,
    on_play_completed,
    on_speech_recognized,
)
import asyncio


async def main() -> None:
    continue_conversation = True

    def _play_media_callback(text: str) -> None:
        logger.info(f"🤖 {text}")

    def _hang_up_callback() -> None:
        nonlocal continue_conversation
        continue_conversation = False
        logger.info("🤖 Hanging up")

    def _transfer_callback() -> None:
        nonlocal continue_conversation
        continue_conversation = False
        logger.info("🤖 Transfering")

    # Mocks
    call = CallStateModel(
        initiate=CallInitiateModel(
            **CONFIG.conversation.initiate.model_dump(),
            phone_number="+33612345678",  # type: ignore
        ),
        lang_shmediumort_code="fr-FR",
        voice_id="dummy",
    )
    automation_client = CallAutomationClientMock(
        hang_up_callback=_hang_up_callback,
        play_media_callback=_play_media_callback,
        transfer_callback=_transfer_callback,
    )
    call_client = automation_client.get_call_connection()

    async def _post_callback(_call: CallStateModel) -> None:
        await on_end_call(call=_call)

    async def _trainings_callback(_call: CallStateModel) -> None:
        await _call.trainings(cache_only=False)

    # Connect call
    await on_call_connected(
        call=call,
        client=automation_client,
    )

    # First IVR
    await on_ivr_recognized(
        call=call,
        client=automation_client,
        label=call.lang.short_code,
        post_callback=_post_callback,
        trainings_callback=_trainings_callback,
    )

    # Simulate conversation
    while continue_conversation:
        message = input("Customer: ")
        if message.strip().lower() == "exit":
            break
        # Respond
        await on_speech_recognized(
            call=call,
            client=automation_client,
            post_callback=_post_callback,
            text=message,
            trainings_callback=_trainings_callback,
        )
        # Receip
        await on_play_completed(
            call=call,
            client=automation_client,
            contexts=call_client.last_contexts,
            post_callback=_post_callback,
        )
        # Reset contexts
        call_client.last_contexts.clear()

    logger.info("Conversation ended, handling disconnection...")

    # Disconnect call
    await on_call_disconnected(
        call=call,
        client=automation_client,
        post_callback=_post_callback,
    )

    logger.info("Bye bye!")


if __name__ == "__main__":
    asyncio.run(main())
