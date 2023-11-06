#!/usr/bin/env python3
"""
This module implements a Remote Two integration driver for Android TV devices.

:copyright: (c) 2023 by Unfolded Circle ApS.
:license: Mozilla Public License Version 2.0, see LICENSE for more details.
"""

import asyncio
import json
import logging
import os
from enum import IntEnum
from typing import Any

import tv
import ucapi
from ucapi import (
    DriverSetupRequest,
    IntegrationSetupError,
    MediaPlayer,
    RequestUserInput,
    SetupAction,
    SetupComplete,
    SetupDriver,
    SetupError,
    UserDataResponse,
    media_player,
)
from zeroconf import ServiceStateChange, Zeroconf
from zeroconf.asyncio import AsyncServiceBrowser, AsyncServiceInfo, AsyncZeroconf

_LOG = logging.getLogger("driver")  # avoid having __main__ in log messages
_LOOP = asyncio.get_event_loop()

_CFG_FILENAME = "config.json"
# Global variables
_CFG_FILE_PATH: str | None = None
_data_path: str | None = None
api = ucapi.IntegrationAPI(_LOOP)
_discovered_android_tvs: dict[str, str] = []
_pairing_android_tv: tv.AndroidTv | None = None
_config: list[dict[str, any]] = []
_configured_android_tvs: dict[str, tv.AndroidTv] = {}


class SetupSteps(IntEnum):
    """Enumeration of setup steps to keep track of user data responses."""

    INIT = 0
    DEVICE_CHOICE = 1
    PAIRING_PIN = 2


_setup_step = SetupSteps.INIT


async def clear_config() -> None:
    """Remove the configuration file and device certificates."""
    global _config
    _config = []

    if os.path.exists(_CFG_FILE_PATH):
        os.remove(_CFG_FILE_PATH)

    if os.path.exists(_data_path + "/androidtv_remote_cert.pem"):
        os.remove(_data_path + "/androidtv_remote_cert.pem")

    if os.path.exists(_data_path + "/androidtv_remote_key.pem"):
        os.remove(_data_path + "/androidtv_remote_key.pem")


async def store_config() -> bool:
    """
    Store the configuration file.

    :return: True if the configuration could be saved.
    """
    try:
        with open(_CFG_FILE_PATH, "w+", encoding="utf-8") as f:
            json.dump(_config, f, ensure_ascii=False)
        return True
    except OSError:
        _LOG.error("Cannot write the config file")

    return False


async def load_config() -> bool:
    """
    Load the config into the config global variable.

    :return: True if the configuration could be loaded.
    """
    global _config

    try:
        with open(_CFG_FILE_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        _config = data
        return True
    except OSError:
        _LOG.error("Cannot open the config file")
    except ValueError:
        _LOG.error("Empty or invalid config file")

    return False


async def _discover_android_tvs(timeout: int = 10) -> None:
    def _on_service_state_changed(
        zeroconf: Zeroconf, service_type: str, name: str, state_change: ServiceStateChange
    ) -> None:
        if state_change is not ServiceStateChange.Added:
            return

        _LOG.debug("Found service: %s, %s", service_type, name)
        _ = asyncio.ensure_future(display_service_info(zeroconf, service_type, name))

    async def display_service_info(zeroconf: Zeroconf, service_type: str, name: str) -> None:
        info = AsyncServiceInfo(service_type, name)
        await info.async_request(zeroconf, 3000)

        if info:
            name_final = name
            name_str = name.split(".")
            if name_str:
                name_final = name_str[0]

            addresses = info.parsed_scoped_addresses()
            if addresses:
                discovered_tv = {"name": name_final, "label": f"{name_final} [{addresses[0]}]", "address": addresses[0]}
                _discovered_android_tvs.append(discovered_tv)
        else:
            _LOG.debug("No info for %s", name)

    aiozc = AsyncZeroconf()
    services = ["_androidtvremote2._tcp.local."]
    _LOG.debug("Looking for Android TV services")

    aiobrowser = AsyncServiceBrowser(aiozc.zeroconf, services, handlers=[_on_service_state_changed])

    await asyncio.sleep(timeout)
    await aiobrowser.async_cancel()
    await aiozc.async_close()
    _LOG.debug("Discovery finished")


# DRIVER SETUP
async def driver_setup_handler(msg: SetupDriver) -> SetupAction:
    """
    Dispatch driver setup requests to corresponding handlers.

    Either start the setup process or handle the selected Android TV device.

    :param msg: the setup driver request object, either DriverSetupRequest or UserDataResponse
    :return: the setup action on how to continue
    """
    global _setup_step

    if isinstance(msg, DriverSetupRequest):
        _setup_step = SetupSteps.INIT
        return await handle_driver_setup(msg)
    if isinstance(msg, UserDataResponse):
        _LOG.debug("UserDataResponse: %s", msg)
        if _setup_step == SetupSteps.DEVICE_CHOICE and "choice" in msg.input_values:
            return await handle_user_data_choice(msg)
        if _setup_step == SetupSteps.PAIRING_PIN and "pin" in msg.input_values:
            return await handle_user_data_pin(msg)
        _LOG.error("No or invalid user response was received: %s", msg)

    # user confirmation not used in setup process
    # if isinstance(msg, UserConfirmationResponse):
    #     return handle_user_confirmation(msg)

    return SetupError()


async def handle_driver_setup(_msg: DriverSetupRequest) -> RequestUserInput | SetupError:
    """
    Start driver setup.

    Initiated by Remote Two to set up the driver.
    Start Android TV discovery and present the found devices to the user to choose from.

    :param _msg: not used, we don't have any input fields in the first setup screen.
    :return: the setup action on how to continue
    """
    global _pairing_android_tv
    global _setup_step

    _LOG.debug("Starting driver setup with Android TV discovery")

    if _pairing_android_tv:
        _pairing_android_tv.disconnect()
        _pairing_android_tv = None
    await clear_config()
    await _discover_android_tvs()
    dropdown_items = []

    for discovered_tv in _discovered_android_tvs:
        tv_data = {"id": discovered_tv["address"], "label": {"en": discovered_tv["label"]}}

        dropdown_items.append(tv_data)

    if not dropdown_items:
        _LOG.warning("No Android TVs found")
        return SetupError(error_type=IntegrationSetupError.NOT_FOUND)

    _setup_step = SetupSteps.DEVICE_CHOICE
    return RequestUserInput(
        {"en": "Please choose your Android TV", "de": "Bitte Android TV auswählen"},
        [
            {
                "field": {"dropdown": {"value": dropdown_items[0]["id"], "items": dropdown_items}},
                "id": "choice",
                "label": {
                    "en": "Choose your Android TV",
                    "de": "Wähle deinen Android TV",
                    "fr": "Choisir votre Android TV",
                },
            }
        ],
    )


async def handle_user_data_choice(msg: UserDataResponse) -> RequestUserInput | SetupError:
    """
    Process user data device choice response in a setup process.

    Driver setup callback to provide requested user data during the setup process.

    :param msg: response data from the requested user data
    :return: the setup action on how to continue.
    """
    global _pairing_android_tv
    global _setup_step

    choice = msg.input_values["choice"]
    _LOG.debug("Chosen Android TV: %s", choice)

    name = ""

    for discovered_tv in _discovered_android_tvs:
        if discovered_tv["address"] == choice:
            name = discovered_tv["name"]

    _pairing_android_tv = tv.AndroidTv(_LOOP, _data_path)
    _LOG.debug("Created new _pairing_android_tv instance")

    res = await _pairing_android_tv.init(choice, name, 30)
    if res is False:
        return SetupError(error_type=IntegrationSetupError.TIMEOUT)

    _LOG.debug("Pairing process begin")

    res = await _pairing_android_tv.start_pairing()
    if res == ucapi.StatusCodes.OK:
        _setup_step = SetupSteps.PAIRING_PIN
        return RequestUserInput(
            {
                "en": "Please enter the PIN shown on your Android TV",
                "de": "Bitte gib die auf deinem Android-Fernseher angezeigte PIN ein",
                "fr": "Veuillez saisir le code PIN affiché sur votre Android TV",
            },
            [{"field": {"text": {"value": "000000"}}, "id": "pin", "label": {"en": "Android TV PIN"}}],
        )

    # no better error code right now
    return SetupError(error_type=IntegrationSetupError.CONNECTION_REFUSED)


async def handle_user_data_pin(msg: UserDataResponse) -> SetupComplete | SetupError:
    """
    Process user data pairing pin response in a setup process.

    Driver setup callback to provide requested user data during the setup process.

    :param msg: response data from the requested user data
    :return: the setup action on how to continue: SetupComplete if a valid Android TV device was chosen.
    """
    global _pairing_android_tv

    _LOG.debug("User has entered the PIN")

    if _pairing_android_tv is None:
        _LOG.error("Can't handle pairing pin: no device instance! Aborting setup")
        return SetupError()

    res = await _pairing_android_tv.finish_pairing(msg.input_values["pin"])

    if res != ucapi.StatusCodes.OK:
        _pairing_android_tv = None
        if res == ucapi.StatusCodes.UNAUTHORIZED:
            return SetupError(error_type=IntegrationSetupError.AUTHORIZATION_ERROR)
        return SetupError(error_type=IntegrationSetupError.CONNECTION_REFUSED)

    _config.append(
        {
            "id": _pairing_android_tv.identifier,
            "name": _pairing_android_tv.name,
            "address": _pairing_android_tv.address,
        }
    )
    await store_config()

    _add_available_android_tv(_pairing_android_tv.identifier, _pairing_android_tv.name)

    _pairing_android_tv.disconnect()
    _pairing_android_tv = None

    await asyncio.sleep(1)
    return SetupComplete()


@api.listens_to(ucapi.Events.CONNECT)
async def on_connect():
    """When the remote connects, we just set the device state."""
    await api.set_device_state(ucapi.DeviceStates.CONNECTED)


@api.listens_to(ucapi.Events.DISCONNECT)
async def on_disconnect():
    """UCR2 disconnect notification."""
    await api.set_device_state(ucapi.DeviceStates.DISCONNECTED)


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

    Connect all Denon AVR instances.
    """
    _LOG.debug("Exit standby event: connecting device(s)")
    # delay is only a temporary workaround, until the core verifies first that the network is up with an IP address
    await asyncio.sleep(2)

    for configured in _configured_android_tvs.values():
        await configured.connect()


@api.listens_to(ucapi.Events.SUBSCRIBE_ENTITIES)
async def on_subscribe_entities(entity_ids):
    """
    Subscribe to given entities.

    :param entity_ids: entity identifiers.
    """
    for entity_id in entity_ids:
        api.configured_entities.update_attributes(
            entity_id, {media_player.Attributes.STATE: media_player.States.UNAVAILABLE}
        )

        host = None
        name = None

        # FIXME add atv_id -> list(entities_id) mapping. Right now the atv_id == entity_id!
        for item in _config:
            if item["id"] == entity_id:
                host = item["address"]
                name = item["name"]

        if host is not None:
            android_tv = tv.AndroidTv(_LOOP, _data_path)
            res = await android_tv.init(host, name)

            if res is False:
                await api.set_device_state(ucapi.DeviceStates.ERROR)
                return  # FIXME what about the other entities? Right now we only have one, but this might change!

            android_tv.events.on(tv.Events.CONNECTED, handle_connected)
            android_tv.events.on(tv.Events.DISCONNECTED, handle_disconnected)
            android_tv.events.on(tv.Events.ERROR, handle_error)
            android_tv.events.on(tv.Events.UPDATE, handle_android_tv_update)

            await android_tv.connect()
            _configured_android_tvs[entity_id] = android_tv
        else:
            _LOG.error("Failed to create Android TV instance")


@api.listens_to(ucapi.Events.UNSUBSCRIBE_ENTITIES)
async def on_unsubscribe_entities(entity_ids):
    """On unsubscribe, we disconnect the devices and remove listeners for events."""
    for entity_id in entity_ids:
        _configured_android_tvs[entity_id].disconnect()
        _configured_android_tvs[entity_id].events.remove_all_listeners()


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
    _LOG.info("Got %s command request: %s %s", entity.id, cmd_id, params)

    # TODO map from device id to entities (see Denon integration)
    # atv_id = _tv_from_entity_id(entity.id)
    # if atv_id is None:
    #     return ucapi.StatusCodes.NOT_FOUND
    atv_id = entity.id

    if atv_id not in _configured_android_tvs:
        _LOG.warning("No Android TV device found for entity: %s", entity.id)
        return ucapi.StatusCodes.SERVICE_UNAVAILABLE

    android_tv = _configured_android_tvs[atv_id]
    res = ucapi.StatusCodes.NOT_IMPLEMENTED

    # TODO might require special handling on the current device state to avoid toggling power state
    if cmd_id == media_player.Commands.ON:
        res = android_tv.turn_on()
    elif cmd_id == media_player.Commands.OFF:
        res = android_tv.turn_off()
    elif cmd_id == media_player.Commands.SELECT_SOURCE:
        if params is None or "source" not in params:
            res = ucapi.StatusCodes.BAD_REQUEST
        else:
            res = android_tv.select_source(params["source"])
    else:
        res = android_tv.send_media_player_command(cmd_id)

    return res


async def handle_connected(identifier: str):
    """Handle Android TV connection."""
    _LOG.debug("Android TV connected: %s", identifier)
    # TODO is this the correct state?
    api.configured_entities.update_attributes(identifier, {media_player.Attributes.STATE: media_player.States.STANDBY})


async def handle_disconnected(identifier: str):
    """Handle Android TV disconnection."""
    _LOG.debug("Android TV disconnected: %s", identifier)
    api.configured_entities.update_attributes(
        identifier, {media_player.Attributes.STATE: media_player.States.UNAVAILABLE}
    )


async def handle_error(identifier: str):
    """Set entities of Android TV to state UNAVAILABLE if connection error occurred."""
    _LOG.debug("Android TV error: %s", identifier)
    api.configured_entities.update_attributes(
        identifier, {media_player.Attributes.STATE: media_player.States.UNAVAILABLE}
    )
    await api.set_device_state(ucapi.DeviceStates.ERROR)


async def handle_android_tv_update(atv_id: str, update: dict[str, Any]) -> None:
    """
    Update attributes of configured media-player entity if AndroidTV properties changed.

    :param atv_id: AndroidTV identifier
    :param update: dictionary containing the updated properties
    """
    _LOG.debug("[%s] ATV update: %s", atv_id, update)

    attributes = {}
    # TODO AndroidTV identifier is currently identical to the one and only exposed media-player entity per device!
    entity_id = atv_id

    configured_entity = api.configured_entities.get(entity_id)
    if configured_entity is None:
        return

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
        api.configured_entities.update_attributes(entity_id, attributes)


def _add_available_android_tv(identifier: str, name: str) -> None:
    entity = media_player.MediaPlayer(
        identifier,
        name,
        [
            media_player.Features.ON_OFF,
            media_player.Features.VOLUME,
            media_player.Features.VOLUME_UP_DOWN,
            media_player.Features.MUTE_TOGGLE,
            media_player.Features.PLAY_PAUSE,
            media_player.Features.NEXT,
            media_player.Features.PREVIOUS,
            media_player.Features.HOME,
            media_player.Features.MENU,
            media_player.Features.CHANNEL_SWITCHER,
            media_player.Features.DPAD,
            media_player.Features.SELECT_SOURCE,
            media_player.Features.MEDIA_TITLE,
            media_player.Features.COLOR_BUTTONS,
            media_player.Features.FAST_FORWARD,
            media_player.Features.REWIND,
        ],
        {
            media_player.Attributes.STATE: media_player.States.UNAVAILABLE,
            media_player.Attributes.VOLUME: 0,
            media_player.Attributes.MUTED: False,
            media_player.Attributes.MEDIA_TITLE: "",
        },
        device_class=media_player.DeviceClasses.TV,
        cmd_handler=media_player_cmd_handler,
    )

    if api.available_entities.contains(entity.id):
        api.available_entities.remove(entity.id)
    api.available_entities.add(entity)


async def main():
    """Start the Remote Two integration driver."""
    global _CFG_FILE_PATH
    global _data_path

    logging.basicConfig()

    level = os.getenv("UC_LOG_LEVEL", "DEBUG").upper()
    logging.getLogger("tv").setLevel(level)
    logging.getLogger("driver").setLevel(level)

    _data_path = api.config_dir_path
    _CFG_FILE_PATH = os.path.join(_data_path, _CFG_FILENAME)

    if await load_config():
        for item in _config:
            _add_available_android_tv(item["id"], item["name"])

    await api.init("driver.json", driver_setup_handler)


if __name__ == "__main__":
    _LOOP.run_until_complete(main())
    _LOOP.run_forever()
