from abc import ABC, abstractmethod
from collections.abc import AsyncIterator
from enum import Enum
from typing import Any
from uuid import UUID

from app.helpers.monitoring import start_as_current_span
from app.helpers.pydantic_types.phone_numbers import PhoneNumber
from app.models.readiness import ReadinessEnum


class CallDirectionEnum(str, Enum):
    """Direction of the call."""

    INBOUND = "inbound"
    """Incoming call."""
    OUTBOUND = "outbound"
    """Outgoing call."""


class CallStatusEnum(str, Enum):
    """Status of the call."""

    CONNECTING = "connecting"
    """Call is being established."""
    CONNECTED = "connected"
    """Call is active."""
    DISCONNECTED = "disconnected"
    """Call has ended."""
    FAILED = "failed"
    """Call failed to establish."""


class AudioFormatEnum(str, Enum):
    """Audio format for streaming."""

    PCM_16KHZ_16BIT_MONO = "pcm_16khz_16bit_mono"
    """16-bit PCM, 16kHz, mono channel (standard telephony)."""


class MediaStreamingTransportEnum(str, Enum):
    """Transport type for media streaming."""

    WEBSOCKET = "websocket"
    """WebSocket transport."""
    RTP = "rtp"
    """Real-time Transport Protocol (SIP)."""


class ITelephony(ABC):
    """
    Interface for telephony operations.

    Abstracts call control, media streaming, and recording across different telephony providers.
    """

    @abstractmethod
    @start_as_current_span("telephony_readiness")
    async def readiness(self) -> ReadinessEnum:
        """
        Check if the telephony service is ready.

        Returns:
            ReadinessEnum: Service readiness status.
        """
        pass

    @abstractmethod
    @start_as_current_span("telephony_answer_call")
    async def answer_call(
        self,
        callback_url: str,
        incoming_context: str,
        phone_number: PhoneNumber,
        wss_url: str,
    ) -> tuple[str, str]:
        """
        Answer an incoming call.

        Args:
            callback_url: URL for call event callbacks
            incoming_context: Context from the incoming call event
            phone_number: Phone number of the caller
            wss_url: WebSocket URL for media streaming

        Returns:
            Tuple of (call_connection_id, server_call_id)
        """
        pass

    @abstractmethod
    @start_as_current_span("telephony_hangup_call")
    async def hangup_call(
        self,
        call_connection_id: str,
    ) -> bool:
        """
        Terminate an active call.

        Args:
            call_connection_id: Unique identifier for the call connection

        Returns:
            True if hangup was successful
        """
        pass

    @abstractmethod
    @start_as_current_span("telephony_transfer_call")
    async def transfer_call(
        self,
        call_connection_id: str,
        target_phone_number: PhoneNumber,
    ) -> bool:
        """
        Transfer the call to another phone number.

        Args:
            call_connection_id: Unique identifier for the call connection
            target_phone_number: Phone number to transfer to

        Returns:
            True if transfer was initiated successfully
        """
        pass

    @abstractmethod
    @start_as_current_span("telephony_start_recording")
    async def start_recording(
        self,
        call_connection_id: str,
        server_call_id: str,
    ) -> str | None:
        """
        Start recording the call.

        Args:
            call_connection_id: Unique identifier for the call connection
            server_call_id: Server-side call identifier

        Returns:
            Recording ID if successful, None otherwise
        """
        pass

    @abstractmethod
    @start_as_current_span("telephony_play_media")
    async def play_media(
        self,
        call_connection_id: str,
        text: str,
        context: str,
        *,
        voice_name: str | None = None,
    ) -> bool:
        """
        Play text-to-speech audio on the call.

        Args:
            call_connection_id: Unique identifier for the call connection
            text: Text to synthesize and play
            context: Context identifier for tracking
            voice_name: Optional voice name for TTS

        Returns:
            True if playback was initiated successfully
        """
        pass

    @abstractmethod
    @start_as_current_span("telephony_recognize_speech")
    async def recognize_speech(
        self,
        call_connection_id: str,
        context: str,
        *,
        choices: list[tuple[str, list[str]]] | None = None,
        max_silence_timeout_ms: int = 5000,
    ) -> bool:
        """
        Start speech recognition (for IVR).

        Args:
            call_connection_id: Unique identifier for the call connection
            context: Context identifier for tracking
            choices: Optional list of (label, phrases) tuples for recognition
            max_silence_timeout_ms: Maximum silence before timeout

        Returns:
            True if recognition was started successfully
        """
        pass

    @abstractmethod
    @start_as_current_span("telephony_start_media_streaming")
    async def start_media_streaming(
        self,
        call_connection_id: str,
    ) -> bool:
        """
        Start media streaming on the call.

        Args:
            call_connection_id: Unique identifier for the call connection

        Returns:
            True if streaming was started successfully
        """
        pass

    @abstractmethod
    async def stream_audio(
        self,
        websocket: Any,  # WebSocket connection (framework-agnostic)
        call_id: UUID,
    ) -> AsyncIterator[bytes]:
        """
        Handle bidirectional audio streaming via WebSocket.

        This is a generator that yields incoming audio chunks and accepts
        outgoing audio via websocket.send().

        Args:
            websocket: WebSocket connection object
            call_id: UUID of the call

        Yields:
            Incoming audio chunks as bytes (PCM 16-bit, 16kHz, mono)
        """
        pass

    @abstractmethod
    @start_as_current_span("telephony_validate_request")
    async def validate_callback_request(
        self,
        authorization: str | None,
        body: str,
    ) -> bool:
        """
        Validate that a callback request is authentic.

        Args:
            authorization: Authorization header value
            body: Request body

        Returns:
            True if request is valid
        """
        pass
