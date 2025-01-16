import asyncio

from aiojobs import Scheduler

from app.helpers.call_events import (
    on_call_connected,
    on_call_disconnected,
    on_end_call,
    on_ivr_recognized,
)
from app.helpers.call_llm import _continue_chat
from app.helpers.config import CONFIG
from app.helpers.logging import logger
from app.models.call import CallInitiateModel, CallStateModel
from app.models.message import MessageModel, PersonaEnum as MessagePersonaEnum
from tests.conftest import CallAutomationClientMock, SpeechSynthesizerMock

_db = CONFIG.database.instance


async def main() -> None:
    continue_conversation = True

    def _play_media_callback(text: str) -> None:
        logger.info("🤖 %s", text)

    def _hang_up_callback() -> None:
        nonlocal continue_conversation
        continue_conversation = False
        logger.info("🤖 Hanging up")

    def _transfer_callback() -> None:
        nonlocal continue_conversation
        continue_conversation = False
        logger.info("🤖 Transfering")

    # Mocks
    tts_client = SpeechSynthesizerMock(
        play_media_callback=_play_media_callback,
    )
    call = CallStateModel(
        initiate=CallInitiateModel(
            **CONFIG.conversation.initiate.model_dump(),
            phone_number="+33612345678",  # pyright: ignore
        ),
        lang_short_code="fr-FR",
        voice_id="dummy",
    )
    automation_client = CallAutomationClientMock(
        hang_up_callback=_hang_up_callback,
        play_media_callback=_play_media_callback,
        transfer_callback=_transfer_callback,
    )
    call_client = automation_client.get_call_connection()

    async with Scheduler() as scheduler:

        async def _post_callback(_call: CallStateModel) -> None:
            await on_end_call(
                call=_call,
                scheduler=scheduler,
            )

        async def _training_callback(_call: CallStateModel) -> None:
            await _call.trainings(cache_only=False)

        # Connect call
        await on_call_connected(
            call=call,
            client=automation_client,
            scheduler=scheduler,
            server_call_id="dummy",
        )

        # First IVR
        await on_ivr_recognized(
            call=call,
            client=automation_client,
            label=call.lang.short_code,
            scheduler=scheduler,
        )

        # Simulate conversation
        while continue_conversation:
            # Get speech
            speech = input("Customer: ")
            if speech.strip().lower() == "exit":
                break

            # Add message to history
            async with _db.call_transac(
                call=call,
                scheduler=scheduler,
            ):
                call.messages.append(
                    MessageModel(
                        content=speech,
                        lang_short_code=call.lang.short_code,
                        persona=MessagePersonaEnum.HUMAN,
                    )
                )

            # Respond
            await _continue_chat(
                call=call,
                client=automation_client,
                post_callback=_post_callback,
                scheduler=scheduler,
                training_callback=_training_callback,
                tts_client=tts_client,
            )

            # Reset contexts
            call_client.last_contexts.clear()

        logger.info("Conversation ended, handling disconnection...")

        # Disconnect call
        await on_call_disconnected(
            call=call,
            client=automation_client,
            post_callback=_post_callback,
            scheduler=scheduler,
        )

    logger.info("Bye bye!")


if __name__ == "__main__":
    asyncio.run(main())
