"""Azure Communication Services implementation of ITelephony interface."""

import asyncio
from base64 import b64decode, b64encode
from collections.abc import AsyncIterator
from contextlib import suppress
from datetime import timedelta
from typing import Any
from uuid import UUID

import jwt
from azure.communication.callautomation import (
    AzureBlobContainerRecordingStorage,
    DtmfTone,
    FileSource,
    MediaStreamingAudioChannelType,
    MediaStreamingContentType,
    MediaStreamingOptions,
    MediaStreamingTransportType,
    PhoneNumberIdentifier,
    RecognitionChoice,
    RecordingChannel,
    RecordingContent,
    RecordingFormat,
    SsmlSource,
    TextSource,
)
from azure.communication.callautomation.aio import CallAutomationClient
from azure.core.credentials import AzureKeyCredential
from azure.core.exceptions import ClientAuthenticationError, HttpResponseError
from fastapi import WebSocketDisconnect

from app.helpers.config_models.communication_services import (
    CommunicationServicesModel,
)
from app.helpers.logging import logger
from app.helpers.pydantic_types.phone_numbers import PhoneNumber
from app.models.readiness import ReadinessEnum
from app.persistence.itelephony import ITelephony


class AzureCommunicationServicesTelephony(ITelephony):
    """
    Azure Communication Services implementation of telephony interface.

    Provides call automation, media streaming, and recording using Azure Communication Services.
    """

    _config: CommunicationServicesModel
    _client: CallAutomationClient | None = None
    _jwks_client: jwt.PyJWKClient | None = None

    def __init__(self, config: CommunicationServicesModel):
        self._config = config

    async def _get_client(self) -> CallAutomationClient:
        """Get or create the call automation client."""
        if not self._client:
            from app.helpers.http import azure_transport

            self._client = CallAutomationClient(
                endpoint=self._config.endpoint,
                transport=await azure_transport(),
                credential=AzureKeyCredential(
                    self._config.access_key.get_secret_value()
                ),
            )
        return self._client

    def _get_jwks_client(self) -> jwt.PyJWKClient:
        """Get or create the JWKS client for JWT validation."""
        if not self._jwks_client:
            self._jwks_client = jwt.PyJWKClient(
                cache_keys=True,
                uri="https://acscallautomation.communication.azure.com/calling/keys",
            )
        return self._jwks_client

    async def readiness(self) -> ReadinessEnum:
        """Check if Azure Communication Services is ready."""
        try:
            client = await self._get_client()
            # Simple check: try to create client
            if client:
                return ReadinessEnum.OK
            return ReadinessEnum.FAIL
        except Exception:
            logger.exception("Azure Communication Services readiness check failed")
            return ReadinessEnum.FAIL

    async def answer_call(
        self,
        callback_url: str,
        incoming_context: str,
        phone_number: PhoneNumber,
        wss_url: str,
    ) -> tuple[str, str]:
        """Answer an incoming call using Azure Communication Services."""
        from app.helpers.config import CONFIG

        client = await self._get_client()

        streaming_options = MediaStreamingOptions(
            audio_channel_type=MediaStreamingAudioChannelType.UNMIXED,
            content_type=MediaStreamingContentType.AUDIO,
            enable_bidirectional=True,
            start_media_streaming=False,
            transport_type=MediaStreamingTransportType.WEBSOCKET,
            transport_url=wss_url,
        )

        try:
            answer_call_result = await client.answer_call(
                callback_url=callback_url,
                cognitive_services_endpoint=CONFIG.cognitive_service.endpoint,
                incoming_call_context=incoming_context,
                media_streaming=streaming_options,
            )
            logger.info("Answered call (%s)", answer_call_result.call_connection_id)
            return (
                answer_call_result.call_connection_id,
                answer_call_result.call_connection_id,  # Azure uses same ID
            )

        except ClientAuthenticationError:
            logger.exception(
                "Authentication error with Communication Services, check the credentials"
            )
            raise

        except HttpResponseError as e:
            if "lifetime validation of the signed http request failed" in e.message.lower():
                logger.debug("Old call event received, ignoring")
            else:
                logger.exception("Unknown error answering call with %s", phone_number)
            raise

    async def hangup_call(self, call_connection_id: str) -> bool:
        """Terminate an active call."""
        try:
            client = await self._get_client()
            call_connection = client.get_call_connection(call_connection_id)
            await call_connection.hang_up(is_for_everyone=True)
            logger.info("Hung up call (%s)", call_connection_id)
            return True
        except Exception:
            logger.exception("Error hanging up call (%s)", call_connection_id)
            return False

    async def transfer_call(
        self,
        call_connection_id: str,
        target_phone_number: PhoneNumber,
    ) -> bool:
        """Transfer the call to another phone number."""
        try:
            client = await self._get_client()
            call_connection = client.get_call_connection(call_connection_id)

            target = PhoneNumberIdentifier(str(target_phone_number))
            await call_connection.transfer_call_to_participant(target_participant=target)

            logger.info(
                "Transferred call (%s) to %s",
                call_connection_id,
                target_phone_number,
            )
            return True
        except Exception:
            logger.exception(
                "Error transferring call (%s) to %s",
                call_connection_id,
                target_phone_number,
            )
            return False

    async def start_recording(
        self,
        call_connection_id: str,
        server_call_id: str,
    ) -> str | None:
        """Start recording the call."""
        try:
            client = await self._get_client()
            call_connection = client.get_call_connection(call_connection_id)

            recording_properties = await call_connection.start_recording(
                recording_channel_type=RecordingChannel.UNMIXED,
                recording_content_type=RecordingContent.AUDIO,
                recording_format_type=RecordingFormat.WAV,
                recording_storage=AzureBlobContainerRecordingStorage(
                    self._config.recording_container_url
                ),
            )

            logger.info(
                "Started recording (%s) for call (%s)",
                recording_properties.recording_id,
                call_connection_id,
            )
            return recording_properties.recording_id

        except Exception:
            logger.exception(
                "Error starting recording for call (%s)",
                call_connection_id,
            )
            return None

    async def play_media(
        self,
        call_connection_id: str,
        text: str,
        context: str,
        *,
        voice_name: str | None = None,
    ) -> bool:
        """Play text-to-speech audio on the call."""
        try:
            from app.helpers.config import CONFIG

            client = await self._get_client()
            call_connection = client.get_call_connection(call_connection_id)

            # Use SSML if voice_name is provided, otherwise plain text
            if voice_name:
                play_source = SsmlSource(
                    ssml_text=f'<speak version="1.0" xmlns="http://www.w3.org/2001/10/synthesis" xml:lang="en-US"><voice name="{voice_name}">{text}</voice></speak>'
                )
            else:
                play_source = TextSource(
                    text=text,
                    voice_name=CONFIG.conversation.initiate.tts_voice,
                )

            await call_connection.play_media_to_all(
                play_source=play_source,
                operation_context=context,
            )

            logger.debug("Playing media on call (%s)", call_connection_id)
            return True

        except Exception:
            logger.exception("Error playing media on call (%s)", call_connection_id)
            return False

    async def recognize_speech(
        self,
        call_connection_id: str,
        context: str,
        *,
        choices: list[tuple[str, list[str]]] | None = None,
        max_silence_timeout_ms: int = 5000,
    ) -> bool:
        """Start speech recognition (for IVR)."""
        try:
            from app.helpers.config import CONFIG

            client = await self._get_client()
            call_connection = client.get_call_connection(call_connection_id)

            # Build recognition choices
            recognition_choices = None
            if choices:
                recognition_choices = [
                    RecognitionChoice(label=label, phrases=phrases)
                    for label, phrases in choices
                ]

            # Start recognition
            await call_connection.start_recognizing_media(
                dtmf_inter_tone_timeout=timedelta(seconds=1),
                dtmf_max_tones_to_collect=1,
                dtmf_stop_tones=[DtmfTone.POUND],
                initial_silence_timeout=timedelta(milliseconds=max_silence_timeout_ms),
                input_type="choices" if choices else "speech",
                interrupt_call_media_operation=False,
                interrupt_prompt=True,
                operation_context=context,
                play_prompt=None,
                speech_language=CONFIG.conversation.initiate.lang.short_code,
                speech_recognition_model_endpoint_id=None,
                target_participant=None,
                **(
                    {"choices": recognition_choices}
                    if recognition_choices
                    else {}
                ),
            )

            logger.debug("Started recognition on call (%s)", call_connection_id)
            return True

        except Exception:
            logger.exception(
                "Error starting recognition on call (%s)", call_connection_id
            )
            return False

    async def start_media_streaming(self, call_connection_id: str) -> bool:
        """Start media streaming on the call."""
        try:
            client = await self._get_client()
            call_connection = client.get_call_connection(call_connection_id)
            await call_connection.start_media_streaming()

            logger.debug("Started media streaming on call (%s)", call_connection_id)
            return True

        except Exception:
            logger.exception(
                "Error starting media streaming on call (%s)", call_connection_id
            )
            return False

    async def stream_audio(
        self,
        websocket: Any,
        call_id: UUID,
    ) -> AsyncIterator[bytes]:
        """
        Handle bidirectional audio streaming via WebSocket.

        Yields incoming audio chunks (PCM 16-bit, 16kHz, mono).
        Accepts outgoing audio via audio_out queue passed to the iterator.
        """
        # This method is a bit different - it's used by the WebSocket handler
        # The actual implementation is in main.py communicationservices_wss_post
        # We don't need to implement it here as it's handled by the framework
        raise NotImplementedError(
            "Audio streaming is handled by WebSocket endpoint in main.py"
        )

    async def validate_callback_request(
        self,
        authorization: str | None,
        body: str,
    ) -> bool:
        """Validate that a callback request is authentic using JWT."""
        if not authorization:
            logger.warning("Authorization header missing")
            return False

        service_jwt = authorization.replace("Bearer ", "")
        try:
            jwt.decode(
                algorithms=["RS256"],
                audience=self._config.resource_id,
                issuer="https://acscallautomation.communication.azure.com",
                jwt=service_jwt,
                leeway=timedelta(minutes=5),
                key=self._get_jwks_client().get_signing_key_from_jwt(service_jwt).key,
            )
            return True
        except jwt.PyJWTError:
            logger.exception("Invalid JWT token")
            return False
