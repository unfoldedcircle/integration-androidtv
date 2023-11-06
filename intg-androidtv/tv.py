"""
This module implements the Android TV communication of the Remote Two integration driver.

:copyright: (c) 2023 by Unfolded Circle ApS.
:license: Mozilla Public License Version 2.0, see LICENSE for more details.
"""

import asyncio
import logging
import time
from asyncio import AbstractEventLoop
from enum import IntEnum

import apps
import inputs
import ucapi
from androidtvremote2 import (
    AndroidTVRemote,
    CannotConnect,
    ConnectionClosed,
    InvalidAuth,
)
from pyee import AsyncIOEventEmitter
from ucapi import media_player

LOG = logging.getLogger(__name__)

BACKOFF_MAX = 30
BACKOFF_SEC = 2


class Events(IntEnum):
    """Internal driver events."""

    CONNECTING = 0
    CONNECTED = 1
    DISCONNECTED = 2
    PAIRED = 3
    AUTH_ERROR = 4
    UPDATE = 5
    VOLUME_CHANGED = 6


# Map media-player entity commands to Android TV key codes
# See https://github.com/tronikos/androidtvremote2/blob/v0.0.14/src/androidtvremote2/remotemessage.proto
MEDIA_PLAYER_COMMANDS = {
    media_player.Commands.ON.value: "POWER",
    media_player.Commands.OFF.value: "POWER",
    media_player.Commands.PLAY_PAUSE.value: "MEDIA_PLAY_PAUSE",
    media_player.Commands.STOP.value: "MEDIA_STOP",
    media_player.Commands.PREVIOUS.value: "MEDIA_PREVIOUS",
    media_player.Commands.NEXT.value: "MEDIA_NEXT",
    media_player.Commands.FAST_FORWARD.value: "MEDIA_FAST_FORWARD",
    media_player.Commands.REWIND.value: "MEDIA_REWIND",
    media_player.Commands.VOLUME_UP.value: "VOLUME_UP",
    media_player.Commands.VOLUME_DOWN.value: "VOLUME_DOWN",
    media_player.Commands.MUTE_TOGGLE.value: "VOLUME_MUTE",
    media_player.Commands.CHANNEL_UP.value: "CHANNEL_UP",
    media_player.Commands.CHANNEL_DOWN.value: "CHANNEL_DOWN",
    media_player.Commands.CURSOR_UP.value: "DPAD_UP",
    media_player.Commands.CURSOR_DOWN.value: "DPAD_DOWN",
    media_player.Commands.CURSOR_LEFT.value: "DPAD_LEFT",
    media_player.Commands.CURSOR_RIGHT.value: "DPAD_RIGHT",
    media_player.Commands.CURSOR_ENTER.value: "DPAD_CENTER",
    media_player.Commands.FUNCTION_RED.value: "PROG_RED",
    media_player.Commands.FUNCTION_GREEN.value: "PROG_GREEN",
    media_player.Commands.FUNCTION_YELLOW.value: "PROG_YELLOW",
    media_player.Commands.FUNCTION_BLUE.value: "PROG_BLUE",
    media_player.Commands.HOME.value: "HOME",
    media_player.Commands.MENU.value: "MENU",  # KEYCODE_TV_CONTENTS_MENU  KEYCODE_TV_MEDIA_CONTEXT_MENU
    media_player.Commands.BACK.value: "BACK",
    media_player.Commands.SEARCH.value: "SEARCH",
}


class AndroidTv:
    """Representing an Android TV device."""

    def __init__(self, loop: AbstractEventLoop, data_path: str):
        """Create instance with given IP address of AVR."""
        self._loop: AbstractEventLoop = loop
        self._data_path: str = data_path
        self.events = AsyncIOEventEmitter(self._loop)
        self._atv: AndroidTVRemote | None = None
        self._identifier: str | None = None
        self._name: str | None = None
        self._mac: str | None = None
        self._address: str | None = None
        self._connection_attempts: int = 0

    def __del__(self):
        """Destructs instance, disconnect AndroidTVRemote."""
        if self._atv:
            self._atv.disconnect()

    async def init(self, host: str, name: str, timeout: int | None = None) -> bool:
        """
        Initialize Android TV instance.

        Connect to the Android TV and create a certificate if missing.

        :param host: IP address of the Android TV.
        :param name: Name of the Android TV device.
        :param timeout: optional timeout in seconds to try connecting to the device.
        :return: True if connected, False if timeout occurred.
        """
        self._atv = AndroidTVRemote(
            client_name="Remote Two",
            certfile=self._data_path + "/androidtv_remote_cert.pem",
            keyfile=self._data_path + "/androidtv_remote_key.pem",
            host=host,
            loop=self._loop,
        )

        start = time.time()

        if await self._atv.async_generate_cert_if_missing():
            LOG.debug("Generated new certificate")

        success = False

        while not success:
            try:
                LOG.debug("Retrieving device information")
                # FIXME async_get_name_and_mac() call hangs for a long time if the device still
                #       shows the previous pairing pin!
                self._name, self._mac = await self._atv.async_get_name_and_mac()
                success = True
                self._connection_attempts = 0
            except (CannotConnect, ConnectionClosed):
                if timeout and time.time() - start > timeout:
                    LOG.error("Abort connecting after %ss: device not reachable", timeout)
                    return False

                self._connection_attempts += 1
                # FIXME #11 backoff delay must deduct time spent in _atv.async_get_name_and_mac()
                backoff = self._backoff()
                LOG.error("Cannot connect, trying again in %ss", backoff)
                await asyncio.sleep(backoff)

        if name != "":
            self._name = name

        self._identifier = self._mac.replace(":", "")
        self._address = host

        # Hook up callbacks
        self._atv.add_is_on_updated_callback(self._is_on_updated)
        self._atv.add_current_app_updated_callback(self._current_app_updated)
        self._atv.add_volume_info_updated_callback(self._volume_info_updated)
        self._atv.add_is_available_updated_callback(self._is_available_updated)

        LOG.debug("Android TV initialised: %s, %s", self._identifier, self._name)
        return True

    @property
    def identifier(self) -> str:
        """Return the device identifier."""
        if not self._identifier:
            raise ValueError("Instance not initialized, no identifier available")
        return self._identifier

    @property
    def name(self) -> str:
        """Return the device name."""
        if not self._identifier:
            raise ValueError("Instance not initialized, no name available")
        return self._name

    @property
    def address(self) -> str:
        """Return the IP address of the device."""
        if not self._identifier:
            raise ValueError("Instance not initialized, no address available")
        return self._address

    def _backoff(self) -> int:
        if self._connection_attempts * BACKOFF_SEC >= BACKOFF_MAX:
            return BACKOFF_MAX
        return self._connection_attempts * BACKOFF_SEC

    async def start_pairing(self) -> ucapi.StatusCodes:
        """
        Start the pairing process.

        :return: OK if started,
                 SERVICE_UNAVAILABLE if connection can't be established,
                 SERVER_ERROR if connection was closed during pairing.
        """
        try:
            await self._atv.async_start_pairing()
            return ucapi.StatusCodes.OK
        except CannotConnect as ex:
            LOG.error("Failed to start pairing. Error connecting: %s", ex)
            return ucapi.StatusCodes.SERVICE_UNAVAILABLE
        except ConnectionClosed as ex:
            # TODO better error code?
            LOG.error("Failed to start pairing. Connection closed: %s", ex)
            return ucapi.StatusCodes.SERVER_ERROR

    async def finish_pairing(self, pin: str) -> ucapi.StatusCodes:
        """
        Finish the pairing process.

        :param pin: pairing code shown on the Android TV.
        :return: OK if succeeded,
                 UNAUTHORIZED if pairing was unsuccessful,
                 SERVICE_UNAVAILABLE if connection was lost, e.g. user pressed cancel on the Android TV.
        """
        try:
            await self._atv.async_finish_pairing(pin)
            return ucapi.StatusCodes.OK
        except InvalidAuth as ex:
            LOG.error("Invalid pairing code. Error: %s", ex)
            return ucapi.StatusCodes.UNAUTHORIZED
        except ConnectionClosed as ex:
            LOG.error("Initialize pair again. Error: %s", ex)
            return ucapi.StatusCodes.SERVICE_UNAVAILABLE

    async def connect(self) -> None:
        """Connect to Android TV."""
        success = False

        while not success:
            try:
                LOG.debug("Connecting Android TV: %s", self._identifier)
                await self._atv.async_connect()
                success = True
                self._connection_attempts = 0
            except InvalidAuth:
                # TODO: In this case we need to re-authenticate
                # How to handle this?
                LOG.error("Invalid auth: %s", self._identifier)
                self.events.emit(Events.AUTH_ERROR, self._identifier)
                break
            except (CannotConnect, ConnectionClosed):
                LOG.error("Android TV device is unreachable on network: %s", self._identifier)
                self._connection_attempts += 1
                # FIXME 11 backoff delay must deduct time spent in _atv.async_connect()
                backoff = self._backoff()
                LOG.debug("Trying again in %s", backoff)
                await asyncio.sleep(backoff)

        if not success:
            return

        self._atv.keep_reconnecting()

        self._update_app_list()

        self.events.emit(Events.CONNECTED, self._identifier)

    def disconnect(self) -> None:
        """Disconnect from Android TV."""
        self._atv.disconnect()
        self.events.emit(Events.DISCONNECTED, self._identifier)

    # Callbacks
    def _is_on_updated(self, is_on: bool) -> None:
        """Notify that the Android TV power state is updated."""
        LOG.info("Device is on: %s", is_on)
        update = {}
        if is_on:
            update["state"] = media_player.States.ON.value
        else:
            update["state"] = media_player.States.OFF.value
        self.events.emit(Events.UPDATE, self._identifier, update)

    def _current_app_updated(self, current_app: str) -> None:
        """Notify that the current app on Android TV is updated."""
        LOG.info("Notified that current_app: %s", current_app)
        update = {"source": current_app}

        if current_app in apps.IdMappings:
            update["source"] = apps.IdMappings[current_app]
        else:
            for query, app in apps.NameMatching.items():
                if query in current_app:
                    update["source"] = app
                    break

        if current_app in ("com.google.android.tvlauncher", "com.android.systemui"):
            update["state"] = media_player.States.ON.value
            update["title"] = ""
        else:
            update["state"] = media_player.States.PLAYING.value
            update["title"] = update["source"]

        self.events.emit(Events.UPDATE, self._identifier, update)

    def _volume_info_updated(self, volume_info: dict[str, str | bool]) -> None:
        """Notify that the Android TV volume information is updated."""
        LOG.info("Notified that volume_info: %s", volume_info)
        update = {"volume": volume_info["level"], "muted": volume_info["muted"]}
        self.events.emit(Events.UPDATE, self._identifier, update)

    def _is_available_updated(self, is_available: bool):
        """Notify that the Android TV is ready to receive commands or is unavailable."""
        LOG.info("Notified that is_available: %s", is_available)
        self.events.emit(Events.CONNECTED if is_available else Events.DISCONNECTED, self.identifier)

    def _update_app_list(self) -> None:
        update = {}
        source_list = []
        for app in apps.Apps:
            source_list.append(app)

        update["source_list"] = source_list
        self.events.emit(Events.UPDATE, self._identifier, update)

    # Commands
    def _send_command(self, key_code: int | str, direction: str = "SHORT") -> ucapi.StatusCodes:
        """
        Send a key press to Android TV.

        This does not block; it buffers the data and arranges for it to be
        sent out asynchronously.

        :param key_code: int (e.g. 26) or str (e.g. "KEYCODE_POWER" or just "POWER")
                         from the enum RemoteKeyCode in remotemessage.proto. See
                         https://github.com/tronikos/androidtvremote2/blob/v0.0.14/src/androidtvremote2/remotemessage.proto#L90
        :param direction: "SHORT" (default) or "START_LONG" or "END_LONG".
        :return: OK if scheduled to be sent,
                 SERVICE_UNAVAILABLE if there's no connection to the device,
                 BAD_REQUEST if the ``key_code`` is unknown
        """  # noqa
        try:
            self._atv.send_key_command(key_code, direction)
            return ucapi.StatusCodes.OK
        except ConnectionClosed:
            LOG.error("Cannot send command, connection lost: %s", self._identifier)
            return ucapi.StatusCodes.SERVICE_UNAVAILABLE
        except ValueError:
            LOG.error("Cannot send command, invalid key_code: %s", key_code)
            return ucapi.StatusCodes.BAD_REQUEST

    def send_media_player_command(self, cmd_id: str) -> ucapi.StatusCodes:
        """
        Send a UCR2 media-player entity command to the Android TV.

        :param cmd_id:
        :return: OK if scheduled to be sent,
                 SERVICE_UNAVAILABLE if there's no connection to the device,
                 BAD_REQUEST if the ``cmd_id`` is unknown or not supported
        """
        try:
            command = media_player.Commands[cmd_id.upper()]

            if command.value in MEDIA_PLAYER_COMMANDS:
                return self._send_command(MEDIA_PLAYER_COMMANDS[command.value])
            LOG.error("Cannot send command, unknown or unsupported command: %s", command)
            return ucapi.StatusCodes.BAD_REQUEST
        except KeyError:
            LOG.error("Cannot send command, unknown media_player command: %s", cmd_id)
            return ucapi.StatusCodes.BAD_REQUEST

    def turn_on(self) -> ucapi.StatusCodes:
        """
        Send power command to AndroidTV device.

        Note: there's no dedicated power-on command!
        """
        return self._send_command("POWER")

    def turn_off(self) -> ucapi.StatusCodes:
        """
        Send power command to AndroidTV device.

        Note: there's no dedicated power-off command!
        """
        return self._send_command("POWER")

    def select_source(self, source: str) -> ucapi.StatusCodes:
        """
        Select a given source, either an app or input.

        :param source: the friendly source name
        """
        if source in apps.Apps:
            return self._launch_app(source)
        if source in inputs.KeyCode:
            return self._switch_input(source)

        LOG.warning(
            "[%s] Unknown source parameter in select_source command: %s",
            self._identifier,
            source,
        )
        return ucapi.StatusCodes.BAD_REQUEST

    def _launch_app(self, app: str) -> ucapi.StatusCodes:
        """Launch an app on Android TV."""
        try:
            self._atv.send_launch_app_command(apps.Apps[app]["url"])
            return ucapi.StatusCodes.OK
        except ConnectionClosed:
            LOG.error("Cannot send command, connection lost: %s", self._identifier)
            return ucapi.StatusCodes.SERVICE_UNAVAILABLE

    def _switch_input(self, source: str) -> ucapi.StatusCodes:
        """
        TEST FUNCTION: Send a KEYCODE_TV_INPUT_* key.

        Uses the inputs.py mappings to map from an input name to a KEYCODE_TV_* key.
        """
        if source in inputs.KeyCode:
            return self._send_command(inputs.KeyCode[source])
        return ucapi.StatusCodes.BAD_REQUEST
