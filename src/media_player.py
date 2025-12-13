"""
Media-player entity functions.

:copyright: (c) 2023 by Unfolded Circle ApS.
:license: Mozilla Public License Version 2.0, see LICENSE for more details.
"""

import logging
from datetime import UTC, datetime
from typing import Any

from ucapi import EntityTypes, MediaPlayer, StatusCodes
from ucapi.media_player import Attributes, Commands, DeviceClasses, Options

import tv
from config import AtvDevice, create_entity_id
from profiles import Profile
from util import handle_entity_state_after_update, key_update_helper

_LOG = logging.getLogger(__name__)


class AndroidTVMediaPlayer(MediaPlayer):  # pylint: disable=too-few-public-methods
    """Representation of a AndroidTV Media Player entity."""

    def __init__(self, device_config: AtvDevice, device: tv.AndroidTv, profile: Profile):
        """Initialize the class."""
        _LOG.debug("[%s] AndroidTVMediaPlayer init", device_config.address)
        self._device = device
        self._device_config = device_config
        self._profile = profile

        entity_id = create_entity_id(device_config.id, EntityTypes.MEDIA_PLAYER)
        attributes = device.attributes
        options: dict[str, Any] = {}
        if profile.simple_commands:
            options[Options.SIMPLE_COMMANDS] = profile.simple_commands
        super().__init__(
            entity_id, device_config.name, profile.features, attributes, device_class=DeviceClasses.TV, options=options
        )

    # pylint: disable=too-many-return-statements
    async def command(self, cmd_id: str, params: dict[str, Any] | None = None) -> StatusCodes:
        """Media-player entity command handler.

        Called by the integration-API if a command is sent to a configured media-player entity.

        :param cmd_id: command
        :param params: optional command parameters
        :return: status code of the command request
        """
        if self._device is None:
            _LOG.warning(
                "Cannot execute command %s %s: no Android TV device found for entity %s",
                cmd_id,
                params if params else "",
                self._device_config.id,
            )
            return StatusCodes.NOT_FOUND

        _LOG.info("[%s] command: %s %s", self._device.log_id, cmd_id, params if params else "")

        if cmd_id == Commands.ON:
            return await self._device.turn_on()
        if cmd_id == Commands.OFF:
            return await self._device.turn_off()
        if cmd_id == Commands.SELECT_SOURCE:
            if params is None or "source" not in params:
                return StatusCodes.BAD_REQUEST
            return await self._device.select_source(params["source"])
        if cmd_id == Commands.VOLUME_UP:
            return await self._device.volume_up()
        if cmd_id == Commands.VOLUME_DOWN:
            return await self._device.volume_down()
        if cmd_id == Commands.MUTE_TOGGLE:
            return await self._device.volume_mute_toggle()
        if cmd_id == Commands.VOLUME:
            return await self._device.volume_set(params.get("volume"))
        if cmd_id == Commands.SEEK:
            return await self._device.media_seek(params.get("media_position", 0))

        return await self._device.send_media_player_command(cmd_id)

    def filter_changed_attributes(self, update: dict[str, Any]) -> dict[str, Any]:
        """
        Filter the given attributes and return only the changed values.

        :param update: dictionary with attributes.
        :return: filtered entity attributes containing changed attributes only.
        """
        attributes = {}

        for attr in [
            Attributes.MEDIA_ARTIST,
            Attributes.MEDIA_ALBUM,
            Attributes.MEDIA_DURATION,
            Attributes.MEDIA_IMAGE_URL,
            Attributes.MEDIA_POSITION,
            Attributes.MEDIA_TITLE,
            Attributes.MUTED,
            Attributes.SOURCE,
            Attributes.SOURCE_LIST,
            Attributes.STATE,
            Attributes.VOLUME,
        ]:
            if attr in update:
                attributes = key_update_helper(attr, update[attr], attributes, self.attributes)

        if Attributes.MEDIA_POSITION in update:
            attributes[Attributes.MEDIA_POSITION_UPDATED_AT] = datetime.now(tz=UTC).isoformat()

        attributes = handle_entity_state_after_update(attributes, self.attributes)

        # TODO check non-playing state logic
        # if Attributes.STATE in attributes:
        #     if attributes[Attributes.STATE] == States.OFF:
        #         attributes[Attributes.MEDIA_IMAGE_URL] = ""
        #         attributes[Attributes.MEDIA_ALBUM] = ""
        #         attributes[Attributes.MEDIA_ARTIST] = ""
        #         attributes[Attributes.MEDIA_TITLE] = ""
        #         attributes[Attributes.MEDIA_TYPE] = ""
        #         attributes[Attributes.SOURCE] = ""

        return attributes
