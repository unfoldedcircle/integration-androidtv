"""
This module implements the Android TV communication of the Remote Two integration driver.

:copyright: (c) 2023 by Unfolded Circle ApS.
:license: Mozilla Public License Version 2.0, see LICENSE for more details.
"""

import asyncio
import logging
import os
import time
from asyncio import AbstractEventLoop, timeout
from enum import IntEnum

import apps
import discover
import inputs
import ucapi
from androidtvremote2 import (
    AndroidTVRemote,
    CannotConnect,
    ConnectionClosed,
    InvalidAuth,
)
from profiles import KeyPress, Profile
from pyee import AsyncIOEventEmitter
from ucapi import media_player

LOG = logging.getLogger(__name__)

BACKOFF_MAX = 30
MIN_RECONNECT_DELAY: float = 0.5
BACKOFF_FACTOR: float = 1.5


class Events(IntEnum):
    """Internal driver events."""

    CONNECTING = 0
    CONNECTED = 1
    DISCONNECTED = 2
    PAIRED = 3
    AUTH_ERROR = 4
    UPDATE = 5
    IP_ADDRESS_CHANGED = 6


class AndroidTv:
    """Representing an Android TV device."""

    def __init__(
        self,
        data_path: str,
        host: str,
        name: str,
        identifier: str | None = None,
        profile: Profile | None = None,
        loop: AbstractEventLoop | None = None,
    ):
        """
        Create instance with given IP address of Android TV device.

        :param data_path: configuration path directory where the client certificates are stored.
        :param host: IP address of the Android TV.
        :param name: Name of the Android TV device.
        :param identifier: Device identifier if known, otherwise init() has to be called.
        :param profile: Device profile used for command mappings.
        :param loop: event loop. Used for connections and futures.
        """
        self._data_path: str = data_path
        self._name: str = name
        self.events = AsyncIOEventEmitter(loop or asyncio.get_running_loop())
        prefix = (
            os.path.join(data_path, f"androidtv_{identifier}_remote_")
            if identifier is not None
            else os.path.join(data_path, "androidtv_remote_")
        )
        self._certfile = prefix + "cert.pem"
        self._keyfile = prefix + "key.pem"

        self._atv: AndroidTVRemote = AndroidTVRemote(
            client_name="Remote Two",
            certfile=self._certfile,
            keyfile=self._keyfile,
            host=host,
            loop=loop or asyncio.get_running_loop(),
        )
        self._connecting: bool = False
        self._identifier: str | None = identifier
        self._profile: Profile | None = profile
        self._connection_attempts: int = 0
        self._reconnect_delay: float = MIN_RECONNECT_DELAY

        # Hook up callbacks
        self._atv.add_is_on_updated_callback(self._is_on_updated)
        self._atv.add_current_app_updated_callback(self._current_app_updated)
        self._atv.add_volume_info_updated_callback(self._volume_info_updated)
        self._atv.add_is_available_updated_callback(self._is_available_updated)

    def __del__(self):
        """Destructs instance, disconnect AndroidTVRemote."""
        self._atv.disconnect()

    async def init(self, max_timeout: int | None = None) -> bool:
        """
        Initialize Android TV instance.

        Connect to the Android TV and create a certificate if missing.

        :param max_timeout: optional maximum timeout in seconds to try connecting to the device. Default: no timeout.
        :return: True if connected or connecting, False if timeout occurred.
        """
        if self._connecting:
            LOG.debug("Skipping init task: connection already running for %s", self._identifier)
            return True

        if await self._atv.async_generate_cert_if_missing():
            LOG.debug("Generated new certificate")

        request_start = None
        success = False
        start = time.time()

        while not success:
            try:
                LOG.debug("Retrieving device information from '%s' on %s", self._name, self._atv.host)
                # Limit connection time for async_get_name_and_mac: if a previous pairing screen is still shown,
                # this would hang for a long time (often minutes)!
                request_start = time.time()
                async with timeout(5.0):
                    name, mac = await self._atv.async_get_name_and_mac()
                success = True
                self._connection_attempts = 0
                self._reconnect_delay = MIN_RECONNECT_DELAY
            except (CannotConnect, ConnectionClosed, asyncio.TimeoutError) as ex:
                if max_timeout and time.time() - start > max_timeout:
                    LOG.error(
                        "Abort connecting after %ss: device '%s' not reachable on %s. %s",
                        max_timeout,
                        self._name,
                        self._atv.host,
                        ex,
                    )
                    return False
                await self._handle_connection_failure(time.time() - request_start, ex)

        if not self._name:
            self._name = name
        self._identifier = mac.replace(":", "")

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
        return self._name

    @property
    def address(self) -> str:
        """Return the IP address of the device."""
        return self._atv.host

    @property
    def device_info(self) -> dict[str, str] | None:
        """Device info (manufacturer, model, sw_version)."""
        return self._atv.device_info

    @property
    def certfile(self) -> str:
        """Return the certificate file  of the device."""
        return self._certfile

    @property
    def keyfile(self) -> str:
        """Return the key file  of the device."""
        return self._keyfile

    @property
    def is_on(self) -> bool | None:
        """Whether the Android TV is on or off. Returns None if not connected."""
        return self._atv.is_on

    def _backoff(self) -> float:
        delay = self._reconnect_delay * BACKOFF_FACTOR
        if delay >= BACKOFF_MAX:
            self._reconnect_delay = BACKOFF_MAX
        else:
            self._reconnect_delay = delay
        return self._reconnect_delay

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

    async def connect(self, max_timeout: int | None = None) -> bool:
        """
        Connect to Android TV.

        :param max_timeout: optional maximum timeout in seconds to try connecting to the device. Default: no timeout.
        :return: True if connected or connecting, False if timeout or authentication error occurred.
        """
        # if we are already connecting, simply ignore further connect calls
        if self._connecting:
            LOG.debug("Connection task already running for %s", self._identifier)
            return

        if self._atv.is_on is not None:
            LOG.debug("Android TV is already connected: %s", self._identifier)
            # just to make sure the state is up-to-date
            self.events.emit(Events.CONNECTED, self._identifier)
            return True

        self._connecting = True
        # disconnect first if we are already connected
        self._atv.disconnect()

        request_start = None
        success = False
        start = time.time()

        while not success:
            try:
                LOG.debug("Connecting Android TV %s on %s", self._identifier, self._atv.host)
                self.events.emit(Events.CONNECTING, self._identifier)
                request_start = time.time()
                async with timeout(5.0):
                    await self._atv.async_connect()
                success = True
                self._connection_attempts = 0
                self._reconnect_delay = MIN_RECONNECT_DELAY
            except InvalidAuth:
                # TODO: In this case we need to re-authenticate
                # How to handle this?
                LOG.error("Invalid authentication for %s", self._identifier)
                self.events.emit(Events.AUTH_ERROR, self._identifier)
                break
            except (CannotConnect, ConnectionClosed, asyncio.TimeoutError) as ex:
                if max_timeout and time.time() - start > max_timeout:
                    LOG.error(
                        "Abort connecting after %ss: device '%s' not reachable on %s. %s",
                        max_timeout,
                        self._name,
                        self._atv.host,
                        ex,
                    )
                    break
                await self._handle_connection_failure(time.time() - request_start, ex)
            except Exception as ex:  # pylint: disable=broad-exception-caught
                LOG.error("Fatal error connecting Android TV %s on %s: %s", self._identifier, self._atv.host, ex)
                break

        if not success:
            self._connecting = False
            return False

        self._atv.keep_reconnecting()

        device_info = self._atv.device_info
        LOG.info("Device information: %s", device_info)

        self._update_app_list()
        self.events.emit(Events.CONNECTED, self._identifier)
        self._connecting = False
        return True

    async def _handle_connection_failure(self, connect_duration: float, ex):
        self._connection_attempts += 1
        # backoff delay must deduct time spent in the connection attempt
        backoff = self._backoff() - connect_duration
        if backoff <= 0:
            backoff = 0.1
        LOG.error(
            "Cannot connect to '%s' on %s, trying again in %.1fs. %s",
            self._identifier if self._identifier else self._name,
            self._atv.host,
            backoff,
            ex,
        )

        # try resolving IP address from device name if we keep failing to connect, maybe the IP address changed
        if self._connection_attempts % 10 == 0:
            LOG.debug("Start resolving IP address for '%s'...", self._name)
            try:
                discovered = await discover.android_tvs()
                for item in discovered:
                    if item["name"] == self._name:
                        if self._atv.host != item["address"]:
                            LOG.info("IP address of '%s' changed: %s", self._name, item["address"])
                            self._atv.host = item["address"]
                            self.events.emit(Events.IP_ADDRESS_CHANGED, self._identifier, self._atv.host)
                            break
            except Exception as e:  # pylint: disable=broad-exception-caught
                # extra safety, otherwise reconnection task is dead
                LOG.error("Discovery failed: %s", e)
        else:
            await asyncio.sleep(backoff)

    def disconnect(self) -> None:
        """Disconnect from Android TV."""
        self._reconnect_delay = MIN_RECONNECT_DELAY
        self._atv.disconnect()
        self.events.emit(Events.DISCONNECTED, self._identifier)

    # Callbacks
    def _is_on_updated(self, is_on: bool) -> None:
        """Notify that the Android TV power state is updated."""
        LOG.info("%s is on: %s", self._identifier, is_on)
        update = {}
        if is_on:
            update["state"] = media_player.States.ON.value
        else:
            update["state"] = media_player.States.OFF.value
        self.events.emit(Events.UPDATE, self._identifier, update)

    def _current_app_updated(self, current_app: str) -> None:
        """Notify that the current app on Android TV is updated."""
        LOG.info("%s notified that current_app: %s", self._identifier, current_app)
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
        LOG.info("%s notified that volume_info: %s", self._identifier, volume_info)
        update = {"volume": volume_info["level"], "muted": volume_info["muted"]}
        self.events.emit(Events.UPDATE, self._identifier, update)

    def _is_available_updated(self, is_available: bool):
        """Notify that the Android TV is ready to receive commands or is unavailable."""
        LOG.info("%s notified that is_available: %s", self._identifier, is_available)
        self.events.emit(Events.CONNECTED if is_available else Events.DISCONNECTED, self.identifier)

    def _update_app_list(self) -> None:
        update = {}
        source_list = []
        for app in apps.Apps:
            source_list.append(app)

        update["source_list"] = source_list
        self.events.emit(Events.UPDATE, self._identifier, update)

    # Commands
    async def _send_command(self, keycode: int | str, action: KeyPress = KeyPress.SHORT) -> ucapi.StatusCodes:
        """
        Send a key press to Android TV.

        This does not block; it buffers the data and arranges for it to be
        sent out asynchronously.

        :param keycode: int (e.g. 26) or str (e.g. "KEYCODE_POWER" or just "POWER")
                         from the enum RemoteKeyCode in remotemessage.proto. See
                         https://github.com/tronikos/androidtvremote2/blob/v0.0.14/src/androidtvremote2/remotemessage.proto#L90
        :param action: key press action type, default = short press
        :return: OK if scheduled to be sent,
                 SERVICE_UNAVAILABLE if there's no connection to the device,
                 BAD_REQUEST if the ``key_code`` is unknown
        """  # noqa
        try:
            if action in (KeyPress.LONG, KeyPress.BEGIN):
                direction = "START_LONG"
            elif action == KeyPress.END:
                direction = "END_LONG"
            else:
                direction = "SHORT"

            if self.is_on is None:
                return ucapi.StatusCodes.SERVICE_UNAVAILABLE

            # workaround for "swallowed commands" since _atv.send_key_command doesn't provide a result
            # pylint: disable=W0212
            if (
                not (self._atv._remote_message_protocol and self._atv._remote_message_protocol.transport)
                or self._atv._remote_message_protocol.transport.is_closing()
            ):
                return ucapi.StatusCodes.SERVICE_UNAVAILABLE

            self._atv.send_key_command(keycode, direction)

            if action == KeyPress.DOUBLE_CLICK:
                self._atv.send_key_command(keycode, direction)
            elif action == KeyPress.LONG:
                await asyncio.sleep(1)
                self._atv.send_key_command(keycode, "END_LONG")

            return ucapi.StatusCodes.OK
        except ConnectionClosed:
            LOG.error("Cannot send command, connection lost: %s", self._identifier)
            return ucapi.StatusCodes.SERVICE_UNAVAILABLE
        except ValueError:
            LOG.error("Cannot send command, invalid key_code: %s", keycode)
            return ucapi.StatusCodes.BAD_REQUEST

    async def send_media_player_command(self, cmd_id: str) -> ucapi.StatusCodes:
        """
        Send a UCR2 media-player entity command to the Android TV.

        :param cmd_id: command identifier
        :return: OK if scheduled to be sent,
                 SERVICE_UNAVAILABLE if there's no connection to the device,
                 BAD_REQUEST if the ``cmd_id`` is unknown or not supported
        """
        if not self._profile:
            LOG.error("Cannot send command %s: no device profile set", cmd_id)
            return ucapi.StatusCodes.SERVER_ERROR

        if command := self._profile.command(cmd_id):
            return await self._send_command(command.keycode, command.action)

        LOG.error("Cannot send command, unknown or unsupported command: %s", cmd_id)
        return ucapi.StatusCodes.BAD_REQUEST

    async def turn_on(self) -> ucapi.StatusCodes:
        """
        Send power command to AndroidTV device if device is in off-state.

        Note: there's no dedicated power-on command! Power handling based on HA integration:
        https://github.com/home-assistant/core/blob/2023.11.0/homeassistant/components/androidtv_remote/media_player.py#L115-L123
        """
        if not self.is_on:
            return await self._send_command("POWER")
        return ucapi.StatusCodes.OK

    async def turn_off(self) -> ucapi.StatusCodes:
        """
        Send power command to AndroidTV device if device is in off-state.

        Note: there's no dedicated power-off command!
        """
        if self.is_on:
            return await self._send_command("POWER")
        return ucapi.StatusCodes.OK

    async def select_source(self, source: str) -> ucapi.StatusCodes:
        """
        Select a given source, either an app or input.

        :param source: the friendly source name
        """
        if source in apps.Apps:
            return self._launch_app(source)
        if source in inputs.KeyCode:
            return await self._switch_input(source)

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
            LOG.error("Cannot launch app, connection lost: %s", self._identifier)
            return ucapi.StatusCodes.SERVICE_UNAVAILABLE

    async def _switch_input(self, source: str) -> ucapi.StatusCodes:
        """
        TEST FUNCTION: Send a KEYCODE_TV_INPUT_* key.

        Uses the inputs.py mappings to map from an input name to a KEYCODE_TV_* key.
        """
        if source in inputs.KeyCode:
            return await self._send_command(inputs.KeyCode[source])
        return ucapi.StatusCodes.BAD_REQUEST
