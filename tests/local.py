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
    on_ivr_recognized,
    on_play_completed,
    on_speech_recognized,
)
import asyncio
from function_app import trainings_event, post_event
from azure.functions import QueueMessage
from signal import SIGINT, SIGTERM


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
        post_callback=lambda _call: post_event(
            QueueMessage(body=_call.model_dump_json())
        ),
        trainings_callback=lambda _call: trainings_event(
            QueueMessage(body=_call.model_dump_json())
        ),
    )

    # Simulate conversation
    while continue_conversation:
        message = input("Customer: ")
        if message.strip().lower() == "exit":
            break
        # Answer
        await on_speech_recognized(
            call=call,
            client=automation_client,
            post_callback=lambda _call: post_event(
                QueueMessage(body=_call.model_dump_json())
            ),
            text=message,
            trainings_callback=lambda _call: trainings_event(
                QueueMessage(body=_call.model_dump_json())
            ),
        )
        # Receip
        await on_play_completed(
            call=call,
            client=automation_client,
            contexts=call_client.last_contexts,
            post_callback=lambda _call: post_event(
                QueueMessage(body=_call.model_dump_json())
            ),
        )
        # Reset contexts
        call_client.last_contexts.clear()

    logger.info("Conversation ended, bye bye")


if __name__ == "__main__":
    loop = asyncio.get_event_loop()
    main_task = asyncio.ensure_future(main())
    for signal in [SIGINT, SIGTERM]:
        loop.add_signal_handler(signal, main_task.cancel)
    try:
        loop.run_until_complete(main_task)
    finally:
        loop.close()
