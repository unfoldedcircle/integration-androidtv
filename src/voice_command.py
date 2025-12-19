"""
This module implements a voice-assistant entity for Android TV voice commands.

:copyright: (c) 2025 by Unfolded Circle ApS.
:license: Mozilla Public License Version 2.0, see LICENSE for more details.
"""

import asyncio
import logging
from collections import defaultdict
from typing import Any

from androidtvremote2 import VoiceStream
from ucapi import (
    AssistantError,
    AssistantErrorCode,
    AssistantEvent,
    AssistantEventType,
    EntityTypes,
    StatusCodes,
    VoiceAssistant,
)
from ucapi.media_player import States as MediaStates
from ucapi.voice_assistant import (
    Attributes,
    AudioConfiguration,
    Commands,
    SampleFormat,
    States,
    VoiceAssistantEntityOptions,
)
from ucapi.voice_stream import (
    VoiceEndReason,
    VoiceSession,
    VoiceSessionClosed,
    VoiceSessionKey,
)

import tv
from config import create_entity_id
from util import handle_entity_state_after_update, key_update_helper

# Only supported audio format for now.
# Follow androidtvremote2 library if more configuration options are reverse engineered.
AUDIO_CHANNELS = 1
AUDIO_SAMPLE_RATE = 8000
AUDIO_SAMPLE_FORMAT = SampleFormat.I16

_LOG = logging.getLogger(__name__)

_voice_stream_sessions = defaultdict[VoiceSessionKey, VoiceStream]()


def va_state_from_atv(device: tv.AndroidTv) -> States:
    """Return the voice-assistant state from the given Android TV device."""
    return va_state_from_media_state(device.player_state)


def va_state_from_media_state(state: MediaStates) -> States:
    """Return the voice-assistant state from the given media player state."""
    match state:
        case MediaStates.OFF:
            return States.OFF
        case MediaStates.UNKNOWN:
            return States.UNKNOWN
        case MediaStates.UNAVAILABLE:
            return States.UNAVAILABLE
        case _:
            return States.ON


class VoiceCommand(VoiceAssistant):
    """Representation of an Android TV voice-assistant entity."""

    def __init__(self, device: tv.AndroidTv, *, api):
        """Initialize the class."""
        self._device = device
        self._api = api

        entity_id = create_entity_id(device.device_config.id, EntityTypes.VOICE_ASSISTANT)
        features = []  # fire and forget voice command only
        super().__init__(
            entity_id,
            f"{device.name} Voice Command",
            features,
            attributes={
                Attributes.STATE: va_state_from_atv(device),
            },
            options=VoiceAssistantEntityOptions(
                audio_cfg=AudioConfiguration(
                    channels=AUDIO_CHANNELS, sample_rate=AUDIO_SAMPLE_RATE, sample_format=AUDIO_SAMPLE_FORMAT
                ),
            ),
        )

    async def command(self, cmd_id: str, params: dict[str, Any] | None = None, *, websocket: Any) -> StatusCodes:
        """
        Voice Assistant entity command handler.

        Called by the integration-API if a command is sent to a configured voice-assistant entity.

        :param websocket:
        :param cmd_id: the entity command
        :param params: optional command parameters
        :param websocket: websocket connection for sending voice-events back to caller.
        :return: status code of the command request
        """
        if params is None:
            return StatusCodes.BAD_REQUEST

        session_id = params.get("session_id", 0)
        if session_id <= 0:
            return StatusCodes.BAD_REQUEST

        if cmd_id == Commands.VOICE_START:
            if self._device.is_on is None:
                return StatusCodes.SERVICE_UNAVAILABLE
            # set up Android TV voice stream as async task to not block the voice_start command
            asyncio.create_task(self._start_voice(websocket, session_id))
            return StatusCodes.OK

        return StatusCodes.BAD_REQUEST

    def filter_changed_attributes(self, update: dict[str, Any]) -> dict[str, Any]:
        """
        Filter the given attributes and return only the changed values.

        :param update: dictionary with attributes.
        :return: filtered entity attributes containing changed attributes only.
        """
        attributes = {}

        if Attributes.STATE in update:
            state = va_state_from_media_state(update[Attributes.STATE])
            attributes = key_update_helper(Attributes.STATE, state, attributes, self.attributes)

        attributes = handle_entity_state_after_update(attributes, self.attributes)
        return attributes

    async def _start_voice(self, websocket: Any, session_id: int) -> None:
        try:
            old_session = _voice_stream_sessions.pop((websocket, session_id), None)
            if old_session is not None:
                old_session.end()
            voice_stream = await self._device.start_voice()
            _voice_stream_sessions[websocket, session_id] = voice_stream
            # Acknowledge start; binary audio will arrive on the WS binary channel
            event = AssistantEvent(
                type=AssistantEventType.READY,
                entity_id=self.id,
                session_id=session_id,
            )
        except asyncio.TimeoutError:
            _LOG.warning("[%s] Timeout: could not start voice session", self._device.log_id)
            event = AssistantEvent(
                type=AssistantEventType.ERROR,
                entity_id=self.id,
                session_id=session_id,
                data=AssistantError(
                    code=AssistantErrorCode.TIMEOUT,
                    message="Timeout starting a voice session with Android TV",
                ),
            )
        except Exception as ex:
            _LOG.error("[%s] Error connecting to device: %s", self._device.log_id, ex)
            event = AssistantEvent(
                type=AssistantEventType.ERROR,
                entity_id=self.id,
                session_id=session_id,
                data=AssistantError(
                    code=AssistantErrorCode.UNEXPECTED_ERROR,
                    message=f"Error connecting to Android TV: {ex}",
                ),
            )

        await self._api.send_assistant_event(websocket, event)


async def on_voice_stream(session: VoiceSession):
    """Voice stream event handler from Integration API."""
    voice_stream = _voice_stream_sessions.pop(session.key, None)
    if voice_stream is None:
        _LOG.warning("No voice stream available for session %d", session.session_id)
        event = AssistantEvent(
            type=AssistantEventType.ERROR,
            entity_id=session.entity_id,
            session_id=session.session_id,
            data=AssistantError(
                code=AssistantErrorCode.SERVICE_UNAVAILABLE,
                message="No voice stream to Android TV available",
            ),
        )
        await session.send_event(event)
        return

    _LOG.info(
        "Voice stream started: session=%d, %dch @ %d Hz %s",
        session.session_id,
        session.config.channels,
        session.config.sample_rate,
        session.config.sample_format,
    )

    # Note: firmware 2.8.1 returns a wrong format!
    if (
        session.config.channels != AUDIO_CHANNELS
        or session.config.sample_rate != AUDIO_SAMPLE_RATE
        or session.config.sample_format != AUDIO_SAMPLE_FORMAT
    ):
        _LOG.error("Unsupported voice stream configuration: %s", session.config)
        voice_stream.end()
        event = AssistantEvent(
            type=AssistantEventType.ERROR,
            entity_id=session.entity_id,
            session_id=session.session_id,
            data=AssistantError(
                code=AssistantErrorCode.INVALID_AUDIO,
                message="Received unsupported voice stream configuration",
            ),
        )
        await session.send_event(event)
        return

    total = 0
    buffer = bytearray()
    try:
        async for chunk in session:
            total += len(chunk)
            # --- Option A) stream directly to Android.
            # ATTENTION: requires patching androidtvremote2 library. VOICE_CHUNK_MIN_SIZE padding needs to be disabled
            # voice_stream.send_chunk(chunk)

            # --- Option B) accumulate chunks until we have at least 8KB
            # Doesn't work reliably: say "mute the volume", Google understands "set an alarm"
            # buffer += chunk
            # if len(buffer) >= androidtvremote2.remote.VOICE_CHUNK_MIN_SIZE:
            #     _LOG.debug("Sending %d bytes of audio data to ATV", len(buffer))
            #
            #     voice_stream.send_chunk(bytes(buffer), 2.0)
            #     buffer.clear()

            # --- Option C) accumulate all chunks, then send it at once. Works best so far...
            buffer += chunk

        # An empty chunk at the end _seems_ to improve the text recognition. No idea why, but without, the last word
        # is often not recognized or missing. If Google only publishes their API!!!!
        buffer += b"\x00" * 4096

        # flush any remaining bytes when the session iterator ends
        if buffer:
            _LOG.debug("Sending final %d bytes of audio data to ATV", len(buffer))
            voice_stream.send_chunk(bytes(buffer))
        _LOG.info("Voice stream ended: session=%d, bytes=%d", session.session_id, total)
    except VoiceSessionClosed as ex:
        _LOG.warning(
            "Voice stream session %d closed! Reason: %s, exception: %s", session.session_id, ex.reason, ex.error
        )
        if ex.reason == VoiceEndReason.REMOTE:
            return  # Remote disconnected
        event = AssistantEvent(
            type=AssistantEventType.ERROR,
            entity_id=session.entity_id,
            session_id=session.session_id,
            data=AssistantError(
                code=(
                    AssistantErrorCode.TIMEOUT
                    if ex.reason == VoiceEndReason.TIMEOUT
                    else AssistantErrorCode.UNEXPECTED_ERROR
                ),
                message=f"Reason: {ex.reason}, exception: {ex.error}",
            ),
        )
        await session.send_event(event)
    finally:
        voice_stream.end()

    # final event
    event = AssistantEvent(
        type=AssistantEventType.FINISHED,
        entity_id=session.entity_id,
        session_id=session.session_id,
    )
    await session.send_event(event)
