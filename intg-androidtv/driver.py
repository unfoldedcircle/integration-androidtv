import asyncio
import json
import logging
import os
from typing import Any

import ucapi.api as uc
from ucapi import entities
from zeroconf import ServiceStateChange, Zeroconf
from zeroconf.asyncio import AsyncServiceBrowser, AsyncServiceInfo, AsyncZeroconf

import tv

LOG = logging.getLogger("driver")  # avoid having __main__ in log messages
LOOP = asyncio.get_event_loop()

_CFG_FILENAME = "config.json"
# Global variables
_CFG_FILE_PATH: str | None = None
_data_path: str | None = None
api = uc.IntegrationAPI(LOOP)
discovered_android_tvs = []
pairing_android_tv = None
_config: list[dict[str, any]] = []
configured_android_tvs: dict[str, tv.AndroidTv] = {}


async def clear_config() -> None:
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
        LOG.error("Cannot write the config file")

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
        LOG.error("Cannot open the config file")
    except ValueError:
        LOG.error("Empty or invalid config file")

    return False


async def discover_android_tvs(timeout: int = 10) -> None:
    def _on_service_state_changed(
        zeroconf: Zeroconf, service_type: str, name: str, state_change: ServiceStateChange
    ) -> None:
        if state_change is not ServiceStateChange.Added:
            return

        LOG.debug("Found service: %s, %s", service_type, name)
        _ = asyncio.ensure_future(display_service_info(zeroconf, service_type, name))

    async def display_service_info(zeroconf: Zeroconf, service_type: str, name: str) -> None:
        info = AsyncServiceInfo(service_type, name)
        await info.async_request(zeroconf, 3000)

        name_final = name
        name_str = name.split(".")
        if name_str:
            name_final = name_str[0]

        discovered_tv = {"name": name_final}

        if info:
            addresses = info.parsed_scoped_addresses()
            if addresses:
                discovered_tv["address"] = addresses[0]
                discovered_android_tvs.append(discovered_tv)
        else:
            LOG.debug("No info for %s", name)

    aiozc = AsyncZeroconf()
    services = ["_androidtvremote2._tcp.local."]
    LOG.debug("Looking for Android TV services")

    aiobrowser = AsyncServiceBrowser(aiozc.zeroconf, services, handlers=[_on_service_state_changed])

    await asyncio.sleep(timeout)
    await aiobrowser.async_cancel()
    await aiozc.async_close()
    LOG.debug("Discovery finished")


# DRIVER SETUP
@api.events.on(uc.uc.EVENTS.SETUP_DRIVER)
async def on_setup_driver(websocket, req_id, _data):
    LOG.debug("Starting driver setup")
    await clear_config()
    await api.acknowledgeCommand(websocket, req_id)
    await api.driverSetupProgress(websocket)

    LOG.debug("Starting Android TV discovery")
    await discover_android_tvs()
    dropdown_items = []

    for discovered_tv in discovered_android_tvs:
        tv_data = {"id": discovered_tv["address"], "label": {"en": discovered_tv["name"]}}

        dropdown_items.append(tv_data)

    if not dropdown_items:
        LOG.warning("No Android TVs found")
        await api.driverSetupError(websocket)
        return

    await api.requestDriverSetupUserInput(
        websocket,
        {"en": "Please choose your Android TV", "de": "Bitte Android TV auswählen"},
        [
            {
                "field": {"dropdown": {"value": dropdown_items[0]["id"], "items": dropdown_items}},
                "id": "choice",
                "label": {"en": "Choose your Android TV", "de": "Wähle deinen Android TV"},
            }
        ],
    )


@api.events.on(uc.uc.EVENTS.SETUP_DRIVER_USER_DATA)
async def on_setup_driver_user_data(websocket, req_id, data):
    global pairing_android_tv

    await api.acknowledgeCommand(websocket, req_id)
    await api.driverSetupProgress(websocket)

    # We pair with companion second
    if "pin" in data:
        LOG.debug("User has entered the PIN")

        res = await pairing_android_tv.finish_pairing(data["pin"])

        if res is False:
            await api.driverSetupError(websocket)
            return

        _config.append(
            {
                "id": pairing_android_tv.identifier,
                "name": pairing_android_tv.name,
                "address": pairing_android_tv.address,
            }
        )
        await store_config()

        add_available_android_tv(pairing_android_tv.identifier, pairing_android_tv.name)

        pairing_android_tv.disconnect()
        pairing_android_tv = None

        await asyncio.sleep(1)
        await api.driverSetupComplete(websocket)

    elif "choice" in data:
        choice = data["choice"]
        LOG.debug("Chosen Android TV: %s", choice)

        name = ""

        for discovered_tv in discovered_android_tvs:
            if discovered_tv["address"] == choice:
                name = discovered_tv["name"]

        pairing_android_tv = tv.AndroidTv(LOOP, _data_path)
        res = await pairing_android_tv.init(choice, name)
        if res is False:
            await api.driverSetupError(websocket)
            return

        LOG.debug("Pairing process begin")

        await api.requestDriverSetupUserInput(
            websocket,
            "Please enter the PIN from your Android TV",
            [{"field": {"text": {"value": "000000"}}, "id": "pin", "label": {"en": "Android TV PIN"}}],
        )

        await pairing_android_tv.start_pairing()

    else:
        LOG.error("No choice was received")
        await api.driverSetupError(websocket)


@api.events.on(uc.uc.EVENTS.CONNECT)
async def on_connect():
    await api.setDeviceState(uc.uc.DEVICE_STATES.CONNECTED)


@api.events.on(uc.uc.EVENTS.DISCONNECT)
async def on_disconnect():
    await api.setDeviceState(uc.uc.DEVICE_STATES.DISCONNECTED)


@api.events.on(uc.uc.EVENTS.ENTER_STANDBY)
async def on_standby():
    """
    Enter standby notification.

    Disconnect every Android TV instances.
    """
    for configured in configured_android_tvs.values():
        configured.disconnect()


@api.events.on(uc.uc.EVENTS.EXIT_STANDBY)
async def on_exit_standby():
    """
    Exit standby notification.

    Connect all Denon AVR instances.
    """
    # delay is only a temporary workaround, until the core verifies first that the network is up with an IP address
    await asyncio.sleep(2)

    for configured in configured_android_tvs.values():
        await configured.connect()


@api.events.on(uc.uc.EVENTS.SUBSCRIBE_ENTITIES)
async def on_subscribe_entities(entity_ids):
    for entity_id in entity_ids:
        api.configuredEntities.updateEntityAttributes(
            entity_id, {entities.media_player.ATTRIBUTES.STATE: entities.media_player.STATES.UNAVAILABLE}
        )

        host = None
        name = None

        # FIXME add atv_id -> list(entities_id) mapping. Right now the atv_id == entity_id!
        for item in _config:
            if item["id"] == entity_id:
                host = item["address"]
                name = item["name"]

        if host is not None:
            android_tv = tv.AndroidTv(LOOP, _data_path)
            res = await android_tv.init(host, name)

            if res is False:
                await api.setDeviceState(uc.uc.DEVICE_STATES.ERROR)
                return  # FIXME what about the other entities? Right now we only have one, but this might change!

            android_tv.events.on(tv.Events.CONNECTED, handle_connected)
            android_tv.events.on(tv.Events.DISCONNECTED, handle_disconnected)
            android_tv.events.on(tv.Events.ERROR, handle_error)
            android_tv.events.on(tv.Events.UPDATE, handle_android_tv_update)

            await android_tv.connect()
            configured_android_tvs[entity_id] = android_tv
        else:
            LOG.error("Failed to create Android TV instance")


@api.events.on(uc.uc.EVENTS.UNSUBSCRIBE_ENTITIES)
async def on_unsubscribe_entities(entity_ids):
    for entity_id in entity_ids:
        configured_android_tvs[entity_id].disconnect()
        configured_android_tvs[entity_id].events.remove_all_listeners()


@api.events.on(uc.uc.EVENTS.ENTITY_COMMAND)
async def on_entity_command(websocket, req_id, entity_id, _entity_type, cmd_id, params):
    if entity_id not in configured_android_tvs:
        await api.acknowledgeCommand(websocket, req_id, uc.uc.STATUS_CODES.NOT_FOUND)
        return

    android_tv = configured_android_tvs[entity_id]

    if cmd_id == entities.media_player.COMMANDS.PLAY_PAUSE:
        res = android_tv.play_pause()
        await api.acknowledgeCommand(
            websocket, req_id, uc.uc.STATUS_CODES.OK if res is True else uc.uc.STATUS_CODES.SERVER_ERROR
        )
    elif cmd_id == entities.media_player.COMMANDS.NEXT:
        res = android_tv.next()
        await api.acknowledgeCommand(
            websocket, req_id, uc.uc.STATUS_CODES.OK if res is True else uc.uc.STATUS_CODES.SERVER_ERROR
        )
    elif cmd_id == entities.media_player.COMMANDS.PREVIOUS:
        res = android_tv.previous()
        await api.acknowledgeCommand(
            websocket, req_id, uc.uc.STATUS_CODES.OK if res is True else uc.uc.STATUS_CODES.SERVER_ERROR
        )
    elif cmd_id == entities.media_player.COMMANDS.VOLUME_UP:
        res = android_tv.volume_up()
        await api.acknowledgeCommand(
            websocket, req_id, uc.uc.STATUS_CODES.OK if res is True else uc.uc.STATUS_CODES.SERVER_ERROR
        )
    elif cmd_id == entities.media_player.COMMANDS.VOLUME_DOWN:
        res = android_tv.volume_down()
        await api.acknowledgeCommand(
            websocket, req_id, uc.uc.STATUS_CODES.OK if res is True else uc.uc.STATUS_CODES.SERVER_ERROR
        )
    elif cmd_id == entities.media_player.COMMANDS.MUTE_TOGGLE:
        res = android_tv.mute_toggle()
        await api.acknowledgeCommand(
            websocket, req_id, uc.uc.STATUS_CODES.OK if res is True else uc.uc.STATUS_CODES.SERVER_ERROR
        )
    elif cmd_id == entities.media_player.COMMANDS.ON:
        res = android_tv.turn_on()
        await api.acknowledgeCommand(
            websocket, req_id, uc.uc.STATUS_CODES.OK if res is True else uc.uc.STATUS_CODES.SERVER_ERROR
        )
    elif cmd_id == entities.media_player.COMMANDS.OFF:
        res = android_tv.turn_off()
        await api.acknowledgeCommand(
            websocket, req_id, uc.uc.STATUS_CODES.OK if res is True else uc.uc.STATUS_CODES.SERVER_ERROR
        )
    elif cmd_id == entities.media_player.COMMANDS.CURSOR_UP:
        res = android_tv.cursor_up()
        await api.acknowledgeCommand(
            websocket, req_id, uc.uc.STATUS_CODES.OK if res is True else uc.uc.STATUS_CODES.SERVER_ERROR
        )
    elif cmd_id == entities.media_player.COMMANDS.CURSOR_DOWN:
        res = android_tv.cursor_down()
        await api.acknowledgeCommand(
            websocket, req_id, uc.uc.STATUS_CODES.OK if res is True else uc.uc.STATUS_CODES.SERVER_ERROR
        )
    elif cmd_id == entities.media_player.COMMANDS.CURSOR_LEFT:
        res = android_tv.cursor_left()
        await api.acknowledgeCommand(
            websocket, req_id, uc.uc.STATUS_CODES.OK if res is True else uc.uc.STATUS_CODES.SERVER_ERROR
        )
    elif cmd_id == entities.media_player.COMMANDS.CURSOR_RIGHT:
        res = android_tv.cursor_right()
        await api.acknowledgeCommand(
            websocket, req_id, uc.uc.STATUS_CODES.OK if res is True else uc.uc.STATUS_CODES.SERVER_ERROR
        )
    elif cmd_id == entities.media_player.COMMANDS.CURSOR_ENTER:
        res = android_tv.cursor_enter()
        await api.acknowledgeCommand(
            websocket, req_id, uc.uc.STATUS_CODES.OK if res is True else uc.uc.STATUS_CODES.SERVER_ERROR
        )
    elif cmd_id == entities.media_player.COMMANDS.HOME:
        res = android_tv.home()
        await api.acknowledgeCommand(
            websocket, req_id, uc.uc.STATUS_CODES.OK if res is True else uc.uc.STATUS_CODES.SERVER_ERROR
        )
    elif cmd_id == entities.media_player.COMMANDS.BACK:
        res = android_tv.back()
        await api.acknowledgeCommand(
            websocket, req_id, uc.uc.STATUS_CODES.OK if res is True else uc.uc.STATUS_CODES.SERVER_ERROR
        )
    elif cmd_id == entities.media_player.COMMANDS.CHANNEL_DOWN:
        res = android_tv.channel_down()
        await api.acknowledgeCommand(
            websocket, req_id, uc.uc.STATUS_CODES.OK if res is True else uc.uc.STATUS_CODES.SERVER_ERROR
        )
    elif cmd_id == entities.media_player.COMMANDS.CHANNEL_UP:
        res = android_tv.channel_up()
        await api.acknowledgeCommand(
            websocket, req_id, uc.uc.STATUS_CODES.OK if res is True else uc.uc.STATUS_CODES.SERVER_ERROR
        )
    elif cmd_id == entities.media_player.COMMANDS.SELECT_SOURCE:
        res = android_tv.launch_app(params["source"])
        await api.acknowledgeCommand(
            websocket, req_id, uc.uc.STATUS_CODES.OK if res is True else uc.uc.STATUS_CODES.SERVER_ERROR
        )


async def handle_connected(identifier):
    LOG.debug("Android TV connected: %s", identifier)
    api.configuredEntities.updateEntityAttributes(
        identifier, {entities.media_player.ATTRIBUTES.STATE: entities.media_player.STATES.STANDBY}
    )


async def handle_disconnected(identifier):
    LOG.debug("Android TV disconnected: %s", identifier)
    api.configuredEntities.updateEntityAttributes(
        identifier, {entities.media_player.ATTRIBUTES.STATE: entities.media_player.STATES.UNAVAILABLE}
    )


async def handle_error(identifier):
    LOG.debug("Android TV error: %s", identifier)
    api.configuredEntities.updateEntityAttributes(
        identifier, {entities.media_player.ATTRIBUTES.STATE: entities.media_player.STATES.UNAVAILABLE}
    )
    await api.setDeviceState(uc.uc.DEVICE_STATES.ERROR)


async def handle_android_tv_update(atv_id: str, update: dict[str, Any]) -> None:
    """
    Update attributes of configured media-player entity if AndroidTV properties changed.

    :param atv_id: AndroidTV identifier
    :param update: dictionary containing the updated properties
    """
    LOG.debug("[%s] ATV update: %s", atv_id, update)

    attributes = {}
    # TODO AndroidTV identifier is currently identical to the one and only exposed media-player entity per device!
    entity_id = atv_id

    configured_entity = api.configuredEntities.getEntity(entity_id)
    if configured_entity is None:
        return

    if "state" in update:
        if update["state"] == "ON":
            attributes[entities.media_player.ATTRIBUTES.STATE] = entities.media_player.STATES.ON
        elif update["state"] == "PLAYING":
            attributes[entities.media_player.ATTRIBUTES.STATE] = entities.media_player.STATES.PLAYING
        else:
            attributes[entities.media_player.ATTRIBUTES.STATE] = entities.media_player.STATES.OFF

    if "title" in update:
        attributes[entities.media_player.ATTRIBUTES.MEDIA_TITLE] = update["title"]

    if "volume" in update:
        attributes[entities.media_player.ATTRIBUTES.VOLUME] = update["volume"]

    if "muted" in update:
        attributes[entities.media_player.ATTRIBUTES.MUTED] = update["muted"]

    if "source_list" in update:
        attributes[entities.media_player.ATTRIBUTES.SOURCE_LIST] = update["source_list"]

    if "source" in update:
        attributes[entities.media_player.ATTRIBUTES.SOURCE] = update["source"]

    if attributes:
        api.configuredEntities.updateEntityAttributes(entity_id, attributes)


def add_available_android_tv(identifier: str, name: str) -> None:
    entity = entities.media_player.MediaPlayer(
        identifier,
        name,
        [
            entities.media_player.FEATURES.ON_OFF,
            entities.media_player.FEATURES.VOLUME,
            entities.media_player.FEATURES.VOLUME_UP_DOWN,
            entities.media_player.FEATURES.MUTE_TOGGLE,
            entities.media_player.FEATURES.PLAY_PAUSE,
            entities.media_player.FEATURES.NEXT,
            entities.media_player.FEATURES.PREVIOUS,
            entities.media_player.FEATURES.HOME,
            entities.media_player.FEATURES.CHANNEL_SWITCHER,
            entities.media_player.FEATURES.DPAD,
            entities.media_player.FEATURES.SELECT_SOURCE,
            entities.media_player.FEATURES.MEDIA_TITLE,
        ],
        {
            entities.media_player.ATTRIBUTES.STATE: entities.media_player.STATES.UNAVAILABLE,
            entities.media_player.ATTRIBUTES.VOLUME: 0,
            entities.media_player.ATTRIBUTES.MUTED: False,
            entities.media_player.ATTRIBUTES.MEDIA_TITLE: "",
        },
        deviceClass=entities.media_player.DEVICECLASSES.TV,
    )

    api.availableEntities.addEntity(entity)


async def main():
    """Start the Remote Two integration driver."""
    global _CFG_FILE_PATH
    global _data_path

    logging.basicConfig()

    level = os.getenv("UC_LOG_LEVEL", "DEBUG").upper()
    logging.getLogger("tv").setLevel(level)
    logging.getLogger("driver").setLevel(level)

    _data_path = api.configDirPath
    _CFG_FILE_PATH = os.path.join(_data_path, _CFG_FILENAME)

    if await load_config():
        for item in _config:
            add_available_android_tv(item["id"], item["name"])

    await api.init("driver.json")


if __name__ == "__main__":
    LOOP.run_until_complete(main())
    LOOP.run_forever()
