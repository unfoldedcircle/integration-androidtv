"""
Media-player entity functions.

:copyright: (c) 2023 by Unfolded Circle ApS.
:license: Mozilla Public License Version 2.0, see LICENSE for more details.
"""

import asyncio
import logging
from asyncio import shield
from typing import Any

from ucapi import EntityTypes, Remote, StatusCodes
from ucapi.media_player import States as MediaStates
from ucapi.remote import Attributes, Commands, Features
from ucapi.remote import States as RemoteStates

import tv
from config import AtvDevice, create_entity_id
from const import REMOTE_BUTTONS_MAPPING, REMOTE_UI_PAGES
from profiles import Profile

_LOG = logging.getLogger(__name__)

# A device state map should be defined and then mapped to both entity types
REMOTE_STATE_MAPPING = {
    MediaStates.OFF: RemoteStates.OFF,
    MediaStates.ON: RemoteStates.ON,
    MediaStates.STANDBY: RemoteStates.ON,
    MediaStates.PLAYING: RemoteStates.ON,
    MediaStates.PAUSED: RemoteStates.ON,
    MediaStates.UNAVAILABLE: RemoteStates.UNAVAILABLE,
    MediaStates.UNKNOWN: RemoteStates.UNKNOWN,
}

COMMAND_TIMEOUT = 4.5


def get_int_param(param: str, params: dict[str, Any], default: int):
    """Get parameter in integer format."""
    # TODO bug to be fixed on UC Core : some params are sent as (empty) strings by remote (hold == "")
    value = params.get(param, default)
    if isinstance(value, str) and len(value) > 0:
        return int(float(value))
    return default


class AndroidTVRemote(Remote):
    """Representation of a AndroidTV Remote entity."""

    def __init__(self, device_config: AtvDevice, device: tv.AndroidTv, profile: Profile):
        """Initialize the class."""
        # pylint: disable = R0801
        _LOG.debug("[%s] AndroidTVRemote init", device_config.address)
        self._device = device
        self._device_config = device_config
        self._profile = profile

        entity_id = create_entity_id(device_config.id, EntityTypes.REMOTE)
        features = [Features.SEND_CMD, Features.ON_OFF]
        attributes = {
            Attributes.STATE: REMOTE_STATE_MAPPING.get(device.player_state),
        }

        super().__init__(
            entity_id,
            device_config.name,
            features,
            attributes,
            simple_commands=profile.simple_commands if profile.simple_commands else [],
            button_mapping=REMOTE_BUTTONS_MAPPING,
            ui_pages=REMOTE_UI_PAGES,
        )

    async def command(self, cmd_id: str, params: dict[str, Any] | None = None) -> StatusCodes:
        """
        Media-player entity command handler.

        Called by the integration-API if a command is sent to a configured media-player entity.

        :param cmd_id: command
        :param params: optional command parameters
        :return: status code of the command request
        """
        _LOG.info("[%s] Got command request: %s %s", self.id, cmd_id, params)
        if self._device is None:
            _LOG.warning("[%s] No AndroidTV instance for this remote entity", self.id)
            return StatusCodes.NOT_FOUND
        res = StatusCodes.OK
        if cmd_id == Commands.ON:
            res = await self._device.turn_on()
        elif cmd_id == Commands.OFF:
            res = await self._device.turn_off()
        elif cmd_id == Commands.TOGGLE:
            if self._device.is_on:
                res = await self._device.turn_off()
            else:
                res = await self._device.turn_on()
        elif cmd_id in [Commands.SEND_CMD, Commands.SEND_CMD_SEQUENCE]:
            # If the duration exceeds the remote timeout, keep it running and return immediately
            try:
                async with asyncio.timeout(COMMAND_TIMEOUT):
                    res = await shield(self.send_commands(cmd_id, params))
            except asyncio.TimeoutError:
                _LOG.info("[%s] Command request timeout, keep running: %s %s", self.id, cmd_id, params)
        else:
            return StatusCodes.NOT_IMPLEMENTED
        return res

    async def send_commands(self, cmd_id: str, params: dict[str, Any] | None = None) -> StatusCodes:
        """Handle custom command or commands sequence."""
        # hold = self.get_int_param("hold", params, 0)
        delay = get_int_param("delay", params, 0)
        repeat = get_int_param("repeat", params, 1)
        command = params.get("command", "")
        res = StatusCodes.OK
        for _i in range(0, repeat):
            if cmd_id == Commands.SEND_CMD:
                result = await self._device.send_media_player_command(command)
                if result != StatusCodes.OK:
                    res = result
                if delay > 0:
                    await asyncio.sleep(delay)
            else:
                commands = params.get("sequence", [])
                for command in commands:
                    result = await self._device.send_media_player_command(command)
                    if result != StatusCodes.OK:
                        res = result
                    if delay > 0:
                        await asyncio.sleep(delay)
        return res

    def filter_changed_attributes(self, update: dict[str, Any]) -> dict[str, Any]:
        """
        Filter the given attributes and return only the changed values.

        :param update: dictionary with attributes.
        :return: filtered entity attributes containing changed attributes only.
        """
        attributes = {}

        if Attributes.STATE in update:
            state = REMOTE_STATE_MAPPING.get(update[Attributes.STATE])
            attributes = key_update_helper(self.attributes, Attributes.STATE, state, attributes)

        _LOG.debug("[%s] AndroidTV remote update attributes %s", self._device_config.id, attributes)
        return attributes


def key_update_helper(input_attributes, key: str, value: str | None, attributes):
    """Return modified attributes only."""
    if value is None:
        return attributes

    if key in input_attributes:
        if input_attributes[key] != value:
            attributes[key] = value
    else:
        attributes[key] = value

    return attributes
