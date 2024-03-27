#!/usr/bin/env python3
"""
This module implements a Remote Two integration driver for Android TV devices.

:copyright: (c) 2023 by Unfolded Circle ApS.
:license: Mozilla Public License Version 2.0, see LICENSE for more details.
"""

import asyncio
import logging
import os
from enum import Enum
from typing import Any

import setup_flow
import tv
import ucapi
from profiles import DeviceProfile, Profile
from ucapi import MediaPlayer, media_player

import config

_LOG = logging.getLogger("driver")  # avoid having __main__ in log messages
_LOOP = asyncio.get_event_loop()

# Global variables
api = ucapi.IntegrationAPI(_LOOP)
_configured_android_tvs: dict[str, tv.AndroidTv] = {}
device_profile = DeviceProfile()


class SimpleCommands(str, Enum):
    """Additional simple commands of the Android TV not covered by media-player features."""

    APP_SWITCHER = "APP_SWITCHER"
    """Show running applications."""
    APPS = "APPS"
    """Show apps."""


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
        # TODO #14 add atv_id -> list(entities_id) mapping. Right now the atv_id == entity_id!
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
    # TODO #14 add entity_id --> atv_id mapping. Right now the atv_id == entity_id!
    for entity_id in entity_ids:
        if entity_id in _configured_android_tvs:
            device = _configured_android_tvs.pop(entity_id)
            device.disconnect()
            device.events.remove_all_listeners()


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
    _LOG.info("Got %s command request: %s %s", entity.id, cmd_id, params if params else "")

    # TODO #14 map from device id to entities (see Denon integration)
    # atv_id = _tv_from_entity_id(entity.id)
    # if atv_id is None:
    #     return ucapi.StatusCodes.NOT_FOUND
    atv_id = entity.id

    configured_entity = api.configured_entities.get(entity.id)

    if configured_entity is None:
        _LOG.warning("No Android TV device found for entity: %s", entity.id)
        return ucapi.StatusCodes.SERVICE_UNAVAILABLE

    android_tv = _configured_android_tvs[atv_id]

    # TODO might require special handling on the current device state to avoid toggling power state
    # https://github.com/home-assistant/core/blob/2023.11.0/homeassistant/components/androidtv_remote/media_player.py#L115-L123
    if cmd_id == media_player.Commands.ON:
        return await android_tv.turn_on()
    if cmd_id == media_player.Commands.OFF:
        return await android_tv.turn_off()
    if cmd_id == media_player.Commands.SELECT_SOURCE:
        if params is None or "source" not in params:
            return ucapi.StatusCodes.BAD_REQUEST
        return await android_tv.select_source(params["source"])

    return await android_tv.send_media_player_command(cmd_id)


async def handle_connected(identifier: str):
    """Handle Android TV connection."""
    _LOG.debug("Android TV connected: %s", identifier)
    # TODO is this the correct state?
    api.configured_entities.update_attributes(identifier, {media_player.Attributes.STATE: media_player.States.STANDBY})
    await api.set_device_state(ucapi.DeviceStates.CONNECTED)  # just to make sure the device state is set


async def handle_disconnected(identifier: str):
    """Handle Android TV disconnection."""
    _LOG.debug("Android TV disconnected: %s", identifier)
    api.configured_entities.update_attributes(
        identifier, {media_player.Attributes.STATE: media_player.States.UNAVAILABLE}
    )


async def handle_authentication_error(identifier: str):
    """Set entities of Android TV to state UNAVAILABLE if authentication error occurred."""
    _LOG.debug("Android TV authentication error: %s", identifier)
    api.configured_entities.update_attributes(
        identifier, {media_player.Attributes.STATE: media_player.States.UNAVAILABLE}
    )
    await api.set_device_state(ucapi.DeviceStates.ERROR)


async def handle_android_tv_address_change(atv_id: str, address: str) -> None:
    """Update device configuration with changed IP address."""
    device = config.devices.get(atv_id)
    if device and device.address != address:
        _LOG.info("Updating IP address %s of configured ATV %s", address, atv_id)
        device.address = address
        config.devices.update(device)


async def handle_android_tv_update(atv_id: str, update: dict[str, Any]) -> None:
    """
    Update attributes of configured media-player entity if AndroidTV properties changed.

    :param atv_id: AndroidTV identifier
    :param update: dictionary containing the updated properties
    """
    _LOG.debug("[%s] ATV update: %s", atv_id, update)

    attributes = {}
    # TODO #14 AndroidTV identifier is currently identical to the one and only exposed media-player entity per device!
    entity_id = atv_id


    # FIXME temporary workaround until ucapi has been refactored:
    #       there's shouldn't be separate lists for available and configured entities
    if api.configured_entities.contains(entity_id):
        configured_entity = api.configured_entities.get(entity_id)
    else:
        configured_entity = api.available_entities.get(entity_id)
    if configured_entity is None:
        return

    old_state = (
        configured_entity.attributes["state"]
        if "state" in configured_entity.attributes
        else media_player.States.UNKNOWN
    )

    if "state" in update:
        if update["state"] == "ON":
            attributes[media_player.Attributes.STATE] = media_player.States.ON
        elif update["state"] == "PLAYING":
            attributes[media_player.Attributes.STATE] = media_player.States.PLAYING
        else:
            attributes[media_player.Attributes.STATE] = media_player.States.OFF

    if "title" in update:
        attributes[media_player.Attributes.MEDIA_TITLE] = update["title"]

    if "volume" in update:
        attributes[media_player.Attributes.VOLUME] = update["volume"]

    if "muted" in update:
        attributes[media_player.Attributes.MUTED] = update["muted"]

    if "source_list" in update:
        attributes[media_player.Attributes.SOURCE_LIST] = update["source_list"]

    if "source" in update:
        attributes[media_player.Attributes.SOURCE] = update["source"]

    if attributes:
        if "state" not in attributes and old_state in (media_player.States.UNAVAILABLE, media_player.States.UNKNOWN):
            attributes[media_player.Attributes.STATE] = media_player.States.ON

        api.configured_entities.update_attributes(entity_id, attributes)


def _add_configured_android_tv(device: config.AtvDevice, connect: bool = True) -> None:
    profile = device_profile.match(device.manufacturer, device.model)

    # the device should not yet be configured, but better be safe
    if device.id in _configured_android_tvs:
        android_tv = _configured_android_tvs[device.id]
        android_tv.disconnect()
    else:
        android_tv = tv.AndroidTv(
            config.devices.data_path, device.address, device.name, device.id, profile=profile, loop=_LOOP
        )
        android_tv.events.on(tv.Events.CONNECTED, handle_connected)
        android_tv.events.on(tv.Events.DISCONNECTED, handle_disconnected)
        android_tv.events.on(tv.Events.AUTH_ERROR, handle_authentication_error)
        android_tv.events.on(tv.Events.UPDATE, handle_android_tv_update)
        android_tv.events.on(tv.Events.IP_ADDRESS_CHANGED, handle_android_tv_address_change)

        _configured_android_tvs[device.id] = android_tv

    async def start_connection():
        res = await android_tv.init()
        if res is False:
            await api.set_device_state(ucapi.DeviceStates.ERROR)
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
    # TODO #14 map entity IDs from device identifier
    entity_id = device.id
    # plain and simple for now: only one media_player per ATV device
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
            # TODO #14 map entity IDs from device identifier
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
    logging.getLogger("discover").setLevel(level)
    logging.getLogger("setup_flow").setLevel(level)

    profile_path = os.path.join(api.config_dir_path, "config/profiles")
    device_profile.load(profile_path)

    config.devices = config.Devices(api.config_dir_path, on_device_added, on_device_removed)
    for device in config.devices.all():
        _add_configured_android_tv(device, connect=False)

    await api.init("driver.json", setup_flow.driver_setup_handler)


if __name__ == "__main__":
    _LOOP.run_until_complete(main())
    _LOOP.run_forever()
