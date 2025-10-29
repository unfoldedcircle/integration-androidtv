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
from datetime import UTC, datetime
from typing import Any

import ucapi
from ucapi.media_player import Attributes as MediaAttr

import config
import media_player
import remote
import setup_flow
import tv
from profiles import DeviceProfile, Profile
from util import filter_data_img_properties

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


async def connect_device(device: tv.AndroidTv):
    """Connect device and send state."""
    try:
        _LOG.debug("Connecting device %s...", device.device_config.id)
        await device.connect()
        _LOG.debug("Device %s connected, sending attributes for subscribed entities", device.device_config.id)
        state = device.state
        for entity in api.configured_entities.get_all():
            entity_id = entity.get("entity_id", "")
            device_id = config.device_from_entity_id(entity_id)
            if device_id != device.device_config.id:
                continue
            # Return all attributes according to entity type
            if isinstance(entity, media_player.AndroidTVMediaPlayer):
                if _LOG.level <= logging.DEBUG:
                    attributes = {
                        k: v for k, v in device.attributes.items() if k != MediaAttr.MEDIA_IMAGE_URL or len(v) < 64
                    }
                    _LOG.debug("Sending attributes %s : %s", entity_id, attributes)
                api.configured_entities.update_attributes(entity_id, device.attributes)
            if isinstance(entity, remote.AndroidTVRemote):
                api.configured_entities.update_attributes(
                    entity_id, {ucapi.remote.Attributes.STATE: remote.REMOTE_STATE_MAPPING.get(state)}
                )
    except RuntimeError as ex:
        _LOG.error("Error while reconnecting to Kodi %s", ex)


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
        entity = api.configured_entities.get(entity_id)
        device_id = config.device_from_entity_id(entity_id)
        if device_id in _configured_android_tvs:
            atv = _configured_android_tvs[device_id]
            if atv.is_on is None:
                state = ucapi.media_player.States.UNAVAILABLE
            else:
                state = ucapi.media_player.States.ON if atv.is_on else ucapi.media_player.States.OFF
            if isinstance(entity, media_player.AndroidTVMediaPlayer):
                api.configured_entities.update_attributes(entity_id, {ucapi.media_player.Attributes.STATE: state})
            if isinstance(entity, remote.AndroidTVRemote):
                api.configured_entities.update_attributes(
                    entity_id, {ucapi.remote.Attributes.STATE: remote.REMOTE_STATE_MAPPING.get(state)}
                )
            _LOOP.create_task(atv.connect())
            continue

        device = config.devices.get(device_id)
        if device:
            _add_configured_android_tv(device)
        else:
            _LOG.error("Failed to subscribe entity %s: no Android TV instance found", entity_id)


@api.listens_to(ucapi.Events.UNSUBSCRIBE_ENTITIES)
async def on_unsubscribe_entities(entity_ids) -> None:
    """On unsubscribe, we disconnect the devices and remove listeners for events."""
    _LOG.debug("Unsubscribe entities event: %s", entity_ids)
    devices_to_remove = set()
    for entity_id in entity_ids:
        device_id = config.device_from_entity_id(entity_id)
        if device_id is None:
            continue
        devices_to_remove.add(device_id)

    # Keep devices that are used by other configured entities not in this list
    for entity in api.configured_entities.get_all():
        entity_id = entity.get("entity_id", "")
        if entity_id in entity_ids:
            continue
        device_id = config.device_from_entity_id(entity_id)
        if device_id is None:
            continue
        if device_id in devices_to_remove:
            devices_to_remove.remove(device_id)

    for device_id in devices_to_remove:
        if device_id in _configured_android_tvs:
            _configured_android_tvs[device_id].disconnect()
            _configured_android_tvs[device_id].events.remove_all_listeners()


async def handle_connected(identifier: str):
    """Handle Android TV connection."""
    device = config.devices.get(identifier)
    if identifier not in _configured_android_tvs:
        _LOG.warning("Device %s is not configured", identifier)
        return

    _LOG.debug("[%s] device connected", device.name if device else identifier)

    for entity_id in _entities_from_device_id(identifier):
        configured_entity = api.configured_entities.get(entity_id)
        if configured_entity is None:
            continue

        if configured_entity.entity_type == ucapi.EntityTypes.MEDIA_PLAYER:
            if (
                configured_entity.attributes[ucapi.media_player.Attributes.STATE]
                == ucapi.media_player.States.UNAVAILABLE
            ):
                # TODO why STANDBY?
                api.configured_entities.update_attributes(
                    entity_id, {ucapi.media_player.Attributes.STATE: ucapi.media_player.States.STANDBY}
                )
            else:
                api.configured_entities.update_attributes(
                    entity_id, {ucapi.media_player.Attributes.STATE: ucapi.media_player.States.ON}
                )
        elif configured_entity.entity_type == ucapi.EntityTypes.REMOTE:
            if configured_entity.attributes[ucapi.remote.Attributes.STATE] == ucapi.remote.States.UNAVAILABLE:
                api.configured_entities.update_attributes(
                    entity_id, {ucapi.remote.Attributes.STATE: ucapi.remote.States.OFF}
                )

        if device and device.auth_error:
            device.auth_error = False
            config.devices.update(device)

        # TODO is this the correct state?
        api.configured_entities.update_attributes(identifier, {MediaAttr.STATE: ucapi.media_player.States.STANDBY})

        await api.set_device_state(ucapi.DeviceStates.CONNECTED)  # just to make sure the device state is set


async def handle_disconnected(identifier: str):
    """Handle Android TV disconnection."""
    for entity_id in _entities_from_device_id(identifier):
        configured_entity = api.configured_entities.get(entity_id)
        if configured_entity is None:
            continue

        if configured_entity.entity_type == ucapi.EntityTypes.MEDIA_PLAYER:
            api.configured_entities.update_attributes(
                entity_id, {ucapi.media_player.Attributes.STATE: ucapi.media_player.States.UNAVAILABLE}
            )
        elif configured_entity.entity_type == ucapi.EntityTypes.REMOTE:
            api.configured_entities.update_attributes(
                entity_id, {ucapi.remote.Attributes.STATE: ucapi.remote.States.UNAVAILABLE}
            )

    if _LOG.isEnabledFor(logging.DEBUG):
        device = config.devices.get(identifier)
        _LOG.debug("[%s] device disconnected", device.name if device else identifier)


async def handle_authentication_error(identifier: str):
    """Set entities of Android TV to state UNAVAILABLE if authentication error occurred."""
    device = config.devices.get(identifier)
    if device and not device.auth_error:
        device.auth_error = True
        config.devices.update(device)

    for entity_id in _entities_from_device_id(identifier):
        configured_entity = api.configured_entities.get(entity_id)
        if configured_entity is None:
            continue

        if configured_entity.entity_type == ucapi.EntityTypes.MEDIA_PLAYER:
            api.configured_entities.update_attributes(
                entity_id, {ucapi.media_player.Attributes.STATE: ucapi.media_player.States.UNAVAILABLE}
            )
        elif configured_entity.entity_type == ucapi.EntityTypes.REMOTE:
            api.configured_entities.update_attributes(
                entity_id, {ucapi.remote.Attributes.STATE: ucapi.remote.States.UNAVAILABLE}
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
    configured_entities = _entities_from_device_id(atv_id)
    if len(configured_entities) == 0:
        _LOG.debug("[%s] ignoring non-configured device update: %s", atv_id, update)
        return

    if _LOG.isEnabledFor(logging.DEBUG):
        device = config.devices.get(atv_id)
        _LOG.debug("[%s] device update: %s", device.name if device else atv_id, filter_data_img_properties(update))

    for entity_id in configured_entities:
        _LOG.info("Update device %s for configured entity %s", atv_id, entity_id)
        configured_entity = api.configured_entities.get(entity_id)
        if configured_entity is None:
            continue
        attributes = {}
        if isinstance(configured_entity, media_player.AndroidTVMediaPlayer):
            old_state = (
                configured_entity.attributes[MediaAttr.STATE]
                if MediaAttr.STATE in configured_entity.attributes
                else ucapi.media_player.States.UNKNOWN
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
                attributes["media_position_updated_at"] = datetime.now(tz=UTC).isoformat()

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
                    ucapi.media_player.States.UNAVAILABLE,
                    ucapi.media_player.States.UNKNOWN,
                ):
                    attributes[MediaAttr.STATE] = ucapi.media_player.States.ON

                api.configured_entities.update_attributes(entity_id, attributes)
            attributes = update
        elif isinstance(configured_entity, remote.AndroidTVRemote):
            attributes = configured_entity.filter_changed_attributes(update)

        if attributes:
            api.configured_entities.update_attributes(entity_id, attributes)


def _entities_from_device_id(device_id: str) -> list[str]:
    """
    Return all associated entity identifiers of the given device.

    :param device_id: the device identifier
    :return: list of entity identifiers
    """
    # dead simple for now: one media_player entity per device!
    # TODO #21 support multiple zones: one media-player per zone
    return [f"media_player.{device_id}", f"remote.{device_id}"]


def _add_configured_android_tv(device_config: config.AtvDevice, connect: bool = True) -> None:
    profile = device_profile.match(device_config.manufacturer, device_config.model, device_config.use_chromecast)

    # the device should not yet be configured, but better be safe
    if device_config.id in _configured_android_tvs:
        android_tv = _configured_android_tvs[device_config.id]
        android_tv.disconnect()
    else:
        android_tv = tv.AndroidTv(
            certfile=config.devices.certfile(device_config.id),
            keyfile=config.devices.keyfile(device_config.id),
            device_config=device_config,
            profile=profile,
            loop=_LOOP,
        )
        android_tv.events.on(tv.Events.CONNECTED, handle_connected)
        android_tv.events.on(tv.Events.DISCONNECTED, handle_disconnected)
        android_tv.events.on(tv.Events.AUTH_ERROR, handle_authentication_error)
        android_tv.events.on(tv.Events.UPDATE, handle_android_tv_update)
        android_tv.events.on(tv.Events.IP_ADDRESS_CHANGED, handle_android_tv_address_change)

        _configured_android_tvs[device_config.id] = android_tv
        _LOG.info(
            "[%s] Configured Android TV device %s with profile and features : %s %s %s",
            device_config.name,
            device_config.id,
            profile.manufacturer,
            profile.model,
            profile.features,
        )

    async def start_connection():
        if await android_tv.init():
            await android_tv.connect()

    if connect:
        # start background task
        _LOOP.create_task(start_connection())

    _register_available_entities(device_config, android_tv, profile)


def _register_available_entities(device_config: config.AtvDevice, device: tv.AndroidTv, profile: Profile) -> None:
    """
    Create entities for given Android TV device and register them as available entities.

    :param device: Android TV configuration
    """
    entities = [
        media_player.AndroidTVMediaPlayer(device_config, device, profile),
        remote.AndroidTVRemote(device_config, device, profile),
    ]
    for entity in entities:
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
            for entity_id in _entities_from_device_id(device.id):
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
    logging.getLogger("media_player").setLevel(level)
    logging.getLogger("remote").setLevel(level)
    logging.getLogger("androidtvremote2").setLevel(level)
    logging.getLogger("external_metadata").setLevel(level)
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
