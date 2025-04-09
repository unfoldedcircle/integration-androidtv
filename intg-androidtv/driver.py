#!/usr/bin/env python3
"""
This module implements a Remote Two integration driver for Android TV devices.

:copyright: (c) 2023-2024 by Unfolded Circle ApS.
:license: Mozilla Public License Version 2.0, see LICENSE for more details.
"""

import asyncio
import logging
import os
import sys
from typing import Any

import setup_flow
import tv
import ucapi
from profiles import DeviceProfile, Profile
from ucapi import MediaPlayer, media_player
from ucapi.media_player import Attributes as MediaAttr

import config

_LOG = logging.getLogger("driver")  # avoid having __main__ in log messages
if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
_LOOP = asyncio.new_event_loop()
asyncio.set_event_loop(_LOOP)

# Global variables
api = ucapi.IntegrationAPI(_LOOP)
_configured_android_tvs: dict[str, tv.AndroidTv] = {}
device_profile = DeviceProfile()


@api.listens_to(ucapi.Events.CONNECT)
async def on_connect():
    """When the UCR2 connects, all configured Android TV devices are getting connected."""
    _LOG.debug("Client connect command: connecting device(s)")
    await api.set_device_state(ucapi.DeviceStates.CONNECTED)  # just to make sure the device state is set
    for atv in _configured_android_tvs.values():
        # start background task
        _LOOP.create_task(atv.connect())


@api.listens_to(ucapi.Events.DISCONNECT)
async def on_disconnect():
    """When the UCR2 disconnects, all configured Android TV devices are disconnected."""
    _LOG.debug("Client disconnect command: disconnecting device(s)")
    for atv in _configured_android_tvs.values():
        atv.disconnect()


@api.listens_to(ucapi.Events.ENTER_STANDBY)
async def on_standby():
    """
    Enter standby notification.

    Disconnect every Android TV instances.
    """
    _LOG.debug("Enter standby event: disconnecting device(s)")
    for configured in _configured_android_tvs.values():
        configured.disconnect()


@api.listens_to(ucapi.Events.EXIT_STANDBY)
async def on_exit_standby():
    """
    Exit standby notification.

    Connect all Android TV instances.
    """
    _LOG.debug("Exit standby event: connecting device(s)")
    for configured in _configured_android_tvs.values():
        # start background task
        _LOOP.create_task(configured.connect())


@api.listens_to(ucapi.Events.SUBSCRIBE_ENTITIES)
async def on_subscribe_entities(entity_ids) -> None:
    """
    Subscribe to given entities.

    :param entity_ids: entity identifiers.
    """
    _LOG.debug("Subscribe entities event: %s", entity_ids)
    for entity_id in entity_ids:
        # Simple mapping at the moment: one entity per device (with the same id)
        atv_id = entity_id
        if atv_id in _configured_android_tvs:
            atv = _configured_android_tvs[atv_id]
            if atv.is_on is None:
                state = media_player.States.UNAVAILABLE
            else:
                state = media_player.States.ON if atv.is_on else media_player.States.OFF
            api.configured_entities.update_attributes(entity_id, {media_player.Attributes.STATE: state})
            _LOOP.create_task(atv.connect())
            continue

        device = config.devices.get(atv_id)
        if device:
            _add_configured_android_tv(device)
        else:
            _LOG.error("Failed to subscribe entity %s: no Android TV instance found", entity_id)


@api.listens_to(ucapi.Events.UNSUBSCRIBE_ENTITIES)
async def on_unsubscribe_entities(entity_ids) -> None:
    """On unsubscribe, we disconnect the devices and remove listeners for events."""
    _LOG.debug("Unsubscribe entities event: %s", entity_ids)
    # Simple mapping at the moment: one entity per device (with the same id)
    for entity_id in entity_ids:
        _configured_android_tvs[entity_id].disconnect()
        _configured_android_tvs[entity_id].events.remove_all_listeners()


# pylint: disable=too-many-return-statements
async def media_player_cmd_handler(
    entity: MediaPlayer, cmd_id: str, params: dict[str, Any] | None
) -> ucapi.StatusCodes:
    """
    Media-player entity command handler.

    Called by the integration-API if a command is sent to a configured media-player entity.

    :param entity: media-player entity
    :param cmd_id: command
    :param params: optional command parameters
    :return:
    """
    # Simple mapping at the moment: one entity per device (with the same id)
    atv_id = entity.id

    if atv_id not in _configured_android_tvs:
        _LOG.warning(
            "Cannot execute command %s %s: no Android TV device found for entity %s",
            cmd_id,
            params if params else "",
            entity.id,
        )
        return ucapi.StatusCodes.NOT_FOUND

    android_tv = _configured_android_tvs[atv_id]

    _LOG.info("[%s] command: %s %s", android_tv.log_id, cmd_id, params if params else "")

    if cmd_id == media_player.Commands.ON:
        return await android_tv.turn_on()
    if cmd_id == media_player.Commands.OFF:
        return await android_tv.turn_off()
    if cmd_id == media_player.Commands.SELECT_SOURCE:
        if params is None or "source" not in params:
            return ucapi.StatusCodes.BAD_REQUEST
        return await android_tv.select_source(params["source"])
    if cmd_id == media_player.Commands.VOLUME_UP:
        return await android_tv.volume_up()
    if cmd_id == media_player.Commands.VOLUME_DOWN:
        return await android_tv.volume_down()
    if cmd_id == media_player.Commands.MUTE_TOGGLE:
        return await android_tv.volume_mute_toggle()
    if cmd_id == media_player.Commands.VOLUME:
        return await android_tv.volume_set(params.get("volume"))
    if cmd_id == media_player.Commands.SEEK:
        return await android_tv.media_seek(params.get("media_position", 0))

    return await android_tv.send_media_player_command(cmd_id)


async def handle_connected(identifier: str):
    """Handle Android TV connection."""
    device = config.devices.get(identifier)
    _LOG.debug("[%s] device connected", device.name if device else identifier)

    if device and device.auth_error:
        device.auth_error = False
        config.devices.update(device)

    # TODO is this the correct state?
    api.configured_entities.update_attributes(identifier, {media_player.Attributes.STATE: media_player.States.STANDBY})
    await api.set_device_state(ucapi.DeviceStates.CONNECTED)  # just to make sure the device state is set


async def handle_disconnected(identifier: str):
    """Handle Android TV disconnection."""
    if _LOG.isEnabledFor(logging.DEBUG):
        device = config.devices.get(identifier)
        _LOG.debug("[%s] device disconnected", device.name if device else identifier)

    api.configured_entities.update_attributes(
        identifier, {media_player.Attributes.STATE: media_player.States.UNAVAILABLE}
    )


async def handle_authentication_error(identifier: str):
    """Set entities of Android TV to state UNAVAILABLE if authentication error occurred."""
    device = config.devices.get(identifier)
    if device and not device.auth_error:
        device.auth_error = True
        config.devices.update(device)

    api.configured_entities.update_attributes(
        identifier, {media_player.Attributes.STATE: media_player.States.UNAVAILABLE}
    )


async def handle_android_tv_address_change(atv_id: str, address: str) -> None:
    """Update device configuration with changed IP address."""
    device = config.devices.get(atv_id)
    if device and device.address != address:
        _LOG.info("[%s] Updating IP address of configured device: %s", atv_id, address)
        device.address = address
        config.devices.update(device)


# pylint: disable=too-many-branches
async def handle_android_tv_update(atv_id: str, update: dict[str, Any]) -> None:
    """
    Update attributes of configured media-player entity if AndroidTV properties changed.

    :param atv_id: AndroidTV identifier
    :param update: dictionary containing the updated properties
    """
    attributes = {}
    # Simple mapping at the moment: one entity per device (with the same id)
    entity_id = atv_id

    configured_entity = api.configured_entities.get(entity_id)
    if configured_entity is None:
        _LOG.debug("[%s] ignoring non-configured device update: %s", atv_id, update)
        return

    if _LOG.isEnabledFor(logging.DEBUG):
        device = config.devices.get(atv_id)
        _LOG.debug("[%s] device update: %s", device.name if device else atv_id, update)

    old_state = (
        configured_entity.attributes[MediaAttr.STATE]
        if MediaAttr.STATE in configured_entity.attributes
        else media_player.States.UNKNOWN
    )

    if MediaAttr.STATE in update and update[MediaAttr.STATE] != old_state:
        attributes[MediaAttr.STATE] = update[MediaAttr.STATE]

    if MediaAttr.MEDIA_TITLE in update:
        attributes[MediaAttr.MEDIA_TITLE] = update[MediaAttr.MEDIA_TITLE]

    if MediaAttr.MEDIA_ALBUM in update:
        attributes[MediaAttr.MEDIA_ALBUM] = update[MediaAttr.MEDIA_ALBUM]

    if MediaAttr.MEDIA_ARTIST in update:
        attributes[MediaAttr.MEDIA_ARTIST] = update[MediaAttr.MEDIA_ARTIST]

    if MediaAttr.MEDIA_POSITION in update:
        attributes[MediaAttr.MEDIA_POSITION] = update[MediaAttr.MEDIA_POSITION]

    if MediaAttr.MEDIA_DURATION in update:
        attributes[MediaAttr.MEDIA_DURATION] = update[MediaAttr.MEDIA_DURATION]

    if MediaAttr.MEDIA_IMAGE_URL in update:
        attributes[MediaAttr.MEDIA_IMAGE_URL] = update[MediaAttr.MEDIA_IMAGE_URL]

    if MediaAttr.VOLUME in update:
        attributes[MediaAttr.VOLUME] = update[MediaAttr.VOLUME]

    if MediaAttr.MUTED in update:
        attributes[MediaAttr.MUTED] = update[MediaAttr.MUTED]

    if MediaAttr.SOURCE_LIST in update:
        attributes[MediaAttr.SOURCE_LIST] = update[MediaAttr.SOURCE_LIST]

    if MediaAttr.SOURCE in update:
        attributes[MediaAttr.SOURCE] = update[MediaAttr.SOURCE]

    if attributes:
        if MediaAttr.STATE not in attributes and old_state in (
            media_player.States.UNAVAILABLE,
            media_player.States.UNKNOWN,
        ):
            attributes[media_player.Attributes.STATE] = media_player.States.ON

        api.configured_entities.update_attributes(entity_id, attributes)


def _add_configured_android_tv(device: config.AtvDevice, connect: bool = True) -> None:
    profile = device_profile.match(device.manufacturer, device.model, device.use_chromecast)

    # the device should not yet be configured, but better be safe
    if device.id in _configured_android_tvs:
        android_tv = _configured_android_tvs[device.id]
        android_tv.disconnect()
    else:
        android_tv = tv.AndroidTv(
            certfile=config.devices.certfile(device.id),
            keyfile=config.devices.keyfile(device.id),
            device_config=device,
            profile=profile,
            loop=_LOOP,
        )
        android_tv.events.on(tv.Events.CONNECTED, handle_connected)
        android_tv.events.on(tv.Events.DISCONNECTED, handle_disconnected)
        android_tv.events.on(tv.Events.AUTH_ERROR, handle_authentication_error)
        android_tv.events.on(tv.Events.UPDATE, handle_android_tv_update)
        android_tv.events.on(tv.Events.IP_ADDRESS_CHANGED, handle_android_tv_address_change)

        _configured_android_tvs[device.id] = android_tv
        _LOG.info(
            "[%s] Configured Android TV device %s with profile and features : %s %s %s",
            device.name,
            device.id,
            profile.manufacturer,
            profile.model,
            profile.features
        )

    async def start_connection():
        if await android_tv.init():
            await android_tv.connect()

    if connect:
        # start background task
        _LOOP.create_task(start_connection())

    _register_available_entities(device, profile)


def _register_available_entities(device: config.AtvDevice, profile: Profile) -> None:
    """
    Create entities for given Android TV device and register them as available entities.

    :param device: Android TV configuration
    """
    # Simple mapping at the moment: one entity per device (with the same id)
    entity_id = device.id
    features = profile.features
    options = {}
    if profile.simple_commands:
        options[media_player.Options.SIMPLE_COMMANDS] = profile.simple_commands

    entity = media_player.MediaPlayer(
        entity_id,
        device.name,
        features,
        {
            media_player.Attributes.STATE: media_player.States.UNKNOWN,
            media_player.Attributes.VOLUME: 0,
            media_player.Attributes.MUTED: False,
            media_player.Attributes.MEDIA_TITLE: "",
            media_player.Attributes.MEDIA_ALBUM: "",
            media_player.Attributes.MEDIA_ARTIST: "",
            media_player.Attributes.MEDIA_POSITION: 0,
            media_player.Attributes.MEDIA_DURATION: 0,
            media_player.Attributes.MEDIA_IMAGE_URL: "",
        },
        device_class=media_player.DeviceClasses.TV,
        options=options,
        cmd_handler=media_player_cmd_handler,
    )

    if api.available_entities.contains(entity.id):
        api.available_entities.remove(entity.id)
    api.available_entities.add(entity)


def on_device_added(device: config.AtvDevice) -> None:
    """Handle a newly added device in the configuration."""
    _LOG.debug("New device added: %s", device)
    _add_configured_android_tv(device, connect=False)


def on_device_removed(device: config.AtvDevice | None) -> None:
    """Handle a removed device in the configuration."""
    if device is None:
        _LOG.debug("Configuration cleared, disconnecting & removing all configured Android TV instances")
        for atv in _configured_android_tvs.values():
            atv.disconnect()
            atv.events.remove_all_listeners()
        _configured_android_tvs.clear()
        api.configured_entities.clear()
        api.available_entities.clear()
    else:
        if device.id in _configured_android_tvs:
            _LOG.debug("Disconnecting from removed Android TV %s", device.id)
            atv = _configured_android_tvs.pop(device.id)
            atv.disconnect()
            atv.events.remove_all_listeners()
            # Simple mapping at the moment: one entity per device (with the same id)
            entity_id = atv.identifier
            api.configured_entities.remove(entity_id)
            api.available_entities.remove(entity_id)


async def main():
    """Start the Remote Two integration driver."""
    logging.basicConfig()  # when running on the device: timestamps are added by the journal
    # logging.basicConfig(
    #     format="%(asctime)s.%(msecs)03d %(levelname)s %(module)s - %(funcName)s: %(message)s",
    #     datefmt="%Y-%m-%d %H:%M:%S",
    # )
    level = os.getenv("UC_LOG_LEVEL", "DEBUG").upper()
    logging.getLogger("tv").setLevel(level)
    logging.getLogger("driver").setLevel(level)
    logging.getLogger("config").setLevel(level)
    logging.getLogger("discover").setLevel(level)
    logging.getLogger("profiles").setLevel(level)
    logging.getLogger("setup_flow").setLevel(level)
    logging.getLogger("androidtvremote2").setLevel(level)
    # logging.getLogger("pychromecast").setLevel(level)

    profile_path = os.path.join(api.config_dir_path, "profiles")
    device_profile.load(profile_path)

    # load paired devices
    config.devices = config.Devices(api.config_dir_path, on_device_added, on_device_removed)
    # best effort migration (if required): network might not be available during startup
    if config.devices.migration_required():
        await config.devices.migrate()
    # and register them as available devices.
    for device in config.devices.all():
        _add_configured_android_tv(device, connect=False)

    await api.init("driver.json", setup_flow.driver_setup_handler)


if __name__ == "__main__":
    _LOOP.run_until_complete(main())
    _LOOP.run_forever()
