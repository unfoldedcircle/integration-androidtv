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
from androidtvremote2 import AndroidTVRemote, CannotConnect, ConnectionClosed, InvalidAuth
from pyee import AsyncIOEventEmitter

LOG = logging.getLogger(__name__)

BACKOFF_MAX = 30
BACKOFF_SEC = 2


class Events(IntEnum):
    """Internal driver events."""

    CONNECTING = 0
    CONNECTED = 1
    DISCONNECTED = 2
    PAIRED = 3
    ERROR = 4
    UPDATE = 5
    VOLUME_CHANGED = 6


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
        LOG.debug("Android TV connecting: %s", self._identifier)

        success = False

        while not success:
            try:
                await self._atv.async_connect()
                success = True
                self._connection_attempts = 0
            except InvalidAuth:
                # TODO: In this case we need to re-authenticate
                # How to handle this?
                LOG.error("Invalid auth: %s", self._identifier)
                self.events.emit(Events.ERROR, self._identifier)
                break
            except (CannotConnect, ConnectionClosed):
                LOG.error("Android TV device is unreachable on network: %s", self._identifier)
                self._connection_attempts += 1
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
            update["state"] = "ON"
        else:
            update["state"] = "OFF"
        self.events.emit(Events.UPDATE, self._identifier, update)

    def _current_app_updated(self, current_app: str) -> None:
        """Notify that the current app on Android TV is updated."""
        LOG.info("Notified that current_app: %s", current_app)
        update = {}

        if current_app in apps.SourceMappings:
            update["source"] = apps.SourceMappings[current_app]
        elif "netflix" in current_app:
            update["source"] = "Netflix"
        elif "youtube" in current_app:
            update["source"] = "YouTube"
        elif "amazonvideo" in current_app:
            update["source"] = "Prime Video"
        elif "hbomax" in current_app:
            update["source"] = "HBO Max"
        elif "disney" in current_app:
            update["source"] = "Disney+"
        elif "apple" in current_app:
            update["source"] = "Apple TV"
        elif "plex" in current_app:
            update["source"] = "Plex"
        elif "kodi" in current_app:
            update["source"] = "Kodi"
        elif "emby" in current_app:
            update["source"] = "Emby"
        else:
            update["source"] = current_app

        if current_app in ("com.google.android.tvlauncher", "com.android.systemui"):
            update["state"] = "ON"
            update["title"] = ""
        else:
            update["state"] = "PLAYING"
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

    def play_pause(self) -> ucapi.StatusCodes:
        """Send Play/Pause media key."""
        return self._send_command("MEDIA_PLAY_PAUSE")

    def next(self) -> ucapi.StatusCodes:
        """Send Play Next media key."""
        return self._send_command("MEDIA_NEXT")

    def previous(self) -> ucapi.StatusCodes:
        """Send Play Previous media key."""
        return self._send_command("MEDIA_PREVIOUS")

    def volume_up(self) -> ucapi.StatusCodes:
        """Send Volume Up key."""
        return self._send_command("VOLUME_UP")

    def volume_down(self) -> ucapi.StatusCodes:
        """Send Volume Down key."""
        return self._send_command("VOLUME_DOWN")

    def mute_toggle(self) -> ucapi.StatusCodes:
        """Send Volume Mute key."""
        return self._send_command("VOLUME_MUTE")

    def cursor_up(self) -> ucapi.StatusCodes:
        """Send Directional Pad Up key."""
        return self._send_command("DPAD_UP")

    def cursor_down(self) -> ucapi.StatusCodes:
        """Send Directional Pad Down key."""
        return self._send_command("DPAD_DOWN")

    def cursor_left(self) -> ucapi.StatusCodes:
        """Send Directional Pad Left key."""
        return self._send_command("DPAD_LEFT")

    def cursor_right(self) -> ucapi.StatusCodes:
        """Send Directional Pad Right key."""
        return self._send_command("DPAD_RIGHT")

    def cursor_enter(self) -> ucapi.StatusCodes:
        """Send Directional Pad Center key."""
        return self._send_command("DPAD_CENTER")

    def home(self) -> ucapi.StatusCodes:
        """Send Home key."""
        return self._send_command("HOME")

    def back(self) -> ucapi.StatusCodes:
        """Send Back key."""
        return self._send_command("BACK")

    def channel_up(self) -> ucapi.StatusCodes:
        """Send Channel up key."""
        return self._send_command("CHANNEL_UP")

    def channel_down(self) -> ucapi.StatusCodes:
        """Send Channel down key."""
        return self._send_command("CHANNEL_DOWN")

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
