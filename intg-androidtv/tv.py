"""
This module implements the Android TV communication of the Remote Two integration driver.

:copyright: (c) 2023-2024 by Unfolded Circle ApS.
:license: Mozilla Public License Version 2.0, see LICENSE for more details.
"""

import asyncio
import logging
import os
import socket
import time
from asyncio import AbstractEventLoop, timeout
from enum import IntEnum
from functools import wraps
from typing import Any, Awaitable, Callable, Concatenate, Coroutine, ParamSpec, TypeVar

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

_LOG = logging.getLogger(__name__)

CONNECTION_TIMEOUT: float = 10.0
"""Android TV device connection timeout in seconds."""
BACKOFF_MAX: int = 30
"""Maximum backoff duration in seconds."""
MIN_RECONNECT_DELAY: float = 0.5
BACKOFF_FACTOR: float = 1.5

LONG_PRESS_DELAY: float = 0.8


class Events(IntEnum):
    """Internal driver events."""

    CONNECTING = 0
    CONNECTED = 1
    DISCONNECTED = 2
    PAIRED = 3
    AUTH_ERROR = 4
    UPDATE = 5
    IP_ADDRESS_CHANGED = 6


class DeviceState(IntEnum):
    """Android TV device connection state."""

    IDLE = 0
    INITIALIZING = 1
    INITIALIZED = 2
    START_PAIRING = 3
    PAIRING_STARTED = 4
    FINISH_PAIRING = 5
    FINISHED_PAIRING = 6
    DISCONNECTED = 10
    CONNECTING = 11
    CONNECTED = 12
    TIMEOUT = 20
    ERROR = 30
    AUTH_ERROR = 31
    PAIRING_ERROR = 32


_AndroidTvT = TypeVar("_AndroidTvT", bound="AndroidTv")
_P = ParamSpec("_P")


# Adapted from Home Assistant `async_log_errors` in
# https://github.com/home-assistant/core/blob/fd1f0b0efeb5231d3ee23d1cb2a10cdeff7c23f1/homeassistant/components/denonavr/media_player.py
def async_handle_atvlib_errors(
    func: Callable[Concatenate[_AndroidTvT, _P], Awaitable[ucapi.StatusCodes | None]],
) -> Callable[Concatenate[_AndroidTvT, _P], Coroutine[Any, Any, ucapi.StatusCodes | None]]:
    """Log errors occurred when calling an Android TV device.

    Decorates methods of AndroidTv class.

    Taken from Home-Assistant
    """

    @wraps(func)
    async def wrapper(self: _AndroidTvT, *args: _P.args, **kwargs: _P.kwargs) -> ucapi.StatusCodes:
        try:
            # use the same exceptions as the func is throwing (e.g. AndroidTVRemote.send_key_command)
            state = self.state
            if state != DeviceState.CONNECTED:
                if state in (DeviceState.DISCONNECTED, DeviceState.CONNECTING) or self.is_on is None:
                    raise ConnectionClosed("Disconnected from device")
                if state in (DeviceState.AUTH_ERROR, DeviceState.PAIRING_ERROR):
                    raise InvalidAuth("Invalid authentication, device requires to be paired again")
                raise CannotConnect(f"Device connection not active (state={state})")

            # workaround for "swallowed commands" since _atv.send_key_command doesn't provide a result
            # pylint: disable=W0212
            if (
                not (self._atv and self._atv._remote_message_protocol and self._atv._remote_message_protocol.transport)
                or self._atv._remote_message_protocol.transport.is_closing()
            ):
                _LOG.warning(
                    "[%s] Cannot send command, remote protocol is no longer active. Resetting connection.",
                    self.log_id,
                )
                self.disconnect()
                self._loop.create_task(self.connect())
                return ucapi.StatusCodes.SERVICE_UNAVAILABLE

            return await func(self, *args, **kwargs)
        except (CannotConnect, ConnectionClosed) as ex:
            _LOG.error("[%s] Cannot send command: %s", self.log_id, ex)
            return ucapi.StatusCodes.SERVICE_UNAVAILABLE
        except InvalidAuth as ex:
            _LOG.error("[%s] Cannot send command: %s", self.log_id, ex)
            return ucapi.StatusCodes.CONFLICT
        except ValueError as ex:
            _LOG.error("[%s] Cannot send command, invalid key_code: %s", self.log_id, ex)
            return ucapi.StatusCodes.BAD_REQUEST

    return wrapper


class AndroidTv:
    """Representing an Android TV device."""

    # pylint: disable=R0917
    def __init__(
        self,
        certfile: str,
        keyfile: str,
        host: str,
        name: str,
        identifier: str | None = None,
        profile: Profile | None = None,
        loop: AbstractEventLoop | None = None,
        use_external_metadata: bool = False,
    ):
        """
        Create instance with given IP address of Android TV device.

        :param certfile: filename that contains the client certificate in PEM format.
        :param keyfile: filename that contains the public key in PEM format.
        :param host: IP address of the Android TV.
        :param name: Name of the Android TV device.
        :param identifier: Device identifier if known, otherwise init() has to be called.
        :param profile: Device profile used for command mappings.
        :param loop: event loop. Used for connections and futures.
        """
        self._state: DeviceState = DeviceState.IDLE
        self._name: str = name
        self._loop: AbstractEventLoop = loop or asyncio.get_running_loop()
        self.events = AsyncIOEventEmitter(self._loop)

        name = os.getenv("UC_CLIENT_NAME", socket.gethostname().split(".", 1)[0])
        self._atv: AndroidTVRemote = AndroidTVRemote(
            client_name=name,
            certfile=certfile,
            keyfile=keyfile,
            host=host,
            loop=self._loop,
        )
        self._identifier: str | None = identifier
        self._profile: Profile | None = profile
        self._connection_attempts: int = 0
        self._reconnect_delay: float = MIN_RECONNECT_DELAY
        self.use_external_metadata: bool = use_external_metadata

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
        if self._state in (DeviceState.INITIALIZING, DeviceState.CONNECTING):
            _LOG.debug("[%s] Skipping init task: connection task already running", self.log_id)
            return True
        self._state = DeviceState.INITIALIZING

        if await self._atv.async_generate_cert_if_missing():
            _LOG.debug("[%s] Generated new certificate", self.log_id)

        request_start = None
        success = False
        start = time.time()

        while not success:
            try:
                _LOG.debug(
                    "[%s] Retrieving device information from %s (timeout=%.1fs)",
                    self.log_id,
                    self._atv.host,
                    CONNECTION_TIMEOUT,
                )
                # Limit connection time for async_get_name_and_mac: if a previous pairing screen is still shown,
                # this would hang for a long time (often minutes)!
                request_start = time.time()
                async with timeout(CONNECTION_TIMEOUT):
                    name, mac = await self._atv.async_get_name_and_mac()
                success = True
                self._connection_attempts = 0
                self._reconnect_delay = MIN_RECONNECT_DELAY
            except (CannotConnect, ConnectionClosed, asyncio.TimeoutError) as ex:
                if max_timeout and time.time() - start > max_timeout:
                    self._state = DeviceState.TIMEOUT
                    _LOG.error(
                        "[%s] Abort connecting after %ds: device %s not reachable on %s. %s",
                        self.log_id,
                        max_timeout,
                        self._identifier,
                        self._atv.host,
                        ex,
                    )
                    return False
                await self._handle_connection_failure(time.time() - request_start, ex)
            except InvalidAuth as ex:
                self._state = DeviceState.AUTH_ERROR
                _LOG.error(
                    "[%s] Authentication error while initializing device %s: %s",
                    self.log_id,
                    self._atv.host,
                    ex,
                )
                return False

        if not self._name:
            self._name = name
        self._identifier = mac.replace(":", "")

        self._state = DeviceState.INITIALIZED
        _LOG.debug("[%s] Android TV initialized", self.log_id)
        return True

    @property
    def state(self) -> DeviceState:
        """Return the device state."""
        return self._state

    @property
    def identifier(self) -> str:
        """
        Return the device identifier.

        :raises ValueError: if no identifier is set
        """
        if not self._identifier:
            raise ValueError("Instance not initialized, no identifier available")
        return self._identifier

    @property
    def log_id(self) -> str:
        """Return a log identifier."""
        return self._name if self._name else self._identifier

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
            self._state = DeviceState.START_PAIRING
            await self._atv.async_start_pairing()
            self._state = DeviceState.PAIRING_STARTED
            return ucapi.StatusCodes.OK
        except (CannotConnect, ConnectionClosed) as ex:
            self._state = DeviceState.PAIRING_ERROR
            _LOG.error("[%s] Failed to start pairing: %s", self.log_id, ex)
            return ucapi.StatusCodes.SERVICE_UNAVAILABLE
        except InvalidAuth as ex:
            self._state = DeviceState.AUTH_ERROR
            _LOG.error("[%s] Authentication error in start pairing: %s", self.log_id, ex)
            return ucapi.StatusCodes.UNAUTHORIZED

    async def finish_pairing(self, pin: str) -> ucapi.StatusCodes:
        """
        Finish the pairing process.

        :param pin: pairing code shown on the Android TV.
        :return: OK if succeeded,
                 UNAUTHORIZED if pairing was unsuccessful,
                 SERVICE_UNAVAILABLE if connection was lost, e.g. user pressed cancel on the Android TV.
        """
        try:
            self._state = DeviceState.FINISH_PAIRING
            await self._atv.async_finish_pairing(pin)
            self._state = DeviceState.FINISHED_PAIRING
            return ucapi.StatusCodes.OK
        except InvalidAuth as ex:
            self._state = DeviceState.AUTH_ERROR
            _LOG.error("[%s] Invalid pairing code. Error: %s", self.log_id, ex)
            return ucapi.StatusCodes.UNAUTHORIZED
        except (CannotConnect, ConnectionClosed) as ex:
            self._state = DeviceState.PAIRING_ERROR
            _LOG.error("[%s] Initialize pair again. Error: %s", self.log_id, ex)
            return ucapi.StatusCodes.SERVICE_UNAVAILABLE

    async def connect(self, max_timeout: int | None = None) -> bool:
        """
        Connect to Android TV.

        :param max_timeout: optional maximum timeout in seconds to try connecting to the device. Default: no timeout.
        :return: True if connected or connecting, False if timeout or authentication error occurred.
        """
        # if we are already connecting, simply ignore further connect calls
        if self._state == DeviceState.CONNECTING:
            _LOG.debug("[%s] Connection task already running", self.log_id)
            return True

        if isinstance(self._atv.is_on, bool) and self._atv.is_on:
            _LOG.debug("[%s] Android TV is already connected", self.log_id)
            # just to make sure the state is up-to-date
            self.events.emit(Events.CONNECTED, self._identifier)
            return True

        self._state = DeviceState.CONNECTING
        # disconnect first for a clean state if the connection is in limbo
        self._atv.disconnect()

        request_start = None
        success = False
        start = time.time()

        while not success:
            try:
                _LOG.debug(
                    "[%s] Connecting Android TV %s on %s (timeout=%.1fs)",
                    self.log_id,
                    self._identifier,
                    self._atv.host,
                    CONNECTION_TIMEOUT,
                )
                self.events.emit(Events.CONNECTING, self._identifier)
                request_start = time.time()
                async with timeout(CONNECTION_TIMEOUT):
                    await self._atv.async_connect()
                success = True
                self._connection_attempts = 0
                self._reconnect_delay = MIN_RECONNECT_DELAY
            except InvalidAuth:
                self._state = DeviceState.AUTH_ERROR
                _LOG.error("[%s] Invalid authentication for %s", self.log_id, self._identifier)
                self.events.emit(Events.AUTH_ERROR, self._identifier)
                break
            except (CannotConnect, ConnectionClosed, asyncio.TimeoutError) as ex:
                if max_timeout and time.time() - start > max_timeout:
                    self._state = DeviceState.TIMEOUT
                    _LOG.error(
                        "[%s] Abort connecting after %ds: device %s not reachable on %s. %s",
                        self.log_id,
                        max_timeout,
                        self._identifier,
                        self._atv.host,
                        ex,
                    )
                    break
                await self._handle_connection_failure(time.time() - request_start, ex)
            except Exception as ex:
                self._state = DeviceState.ERROR
                _LOG.error(
                    "[%s] Fatal error connecting Android TV %s on %s: %s",
                    self.log_id,
                    self._identifier,
                    self._atv.host,
                    ex,
                )
                break

        if not success:
            if self._state == DeviceState.CONNECTING:
                self._state = DeviceState.ERROR
            return False

        def _handle_invalid_auth() -> None:
            self._state = DeviceState.AUTH_ERROR
            _LOG.error("[%s] Invalid authentication for %s while reconnecting", self.log_id, self._identifier)
            self.events.emit(Events.AUTH_ERROR, self._identifier)

        self._atv.keep_reconnecting(_handle_invalid_auth)

        device_info = self._atv.device_info
        _LOG.info("[%s] Device information: %s", self.log_id, device_info)

        self._update_app_list()
        self._state = DeviceState.CONNECTED
        self.events.emit(Events.CONNECTED, self._identifier)
        return True

    async def _handle_connection_failure(self, connect_duration: float, ex):
        self._connection_attempts += 1
        # backoff delay must deduct time spent in the connection attempt
        backoff = self._backoff() - connect_duration
        if backoff <= 0:
            backoff = 0.1
        _LOG.error(
            "[%s] Cannot connect to %s on %s, trying again in %.1fs. %s",
            self.log_id,
            self._identifier if self._identifier else self._name,
            self._atv.host,
            backoff,
            ex,
        )

        # try resolving IP address from device name if we keep failing to connect, maybe the IP address changed
        if self._connection_attempts % 10 == 0:
            _LOG.debug("[%s] Start resolving IP address for %s...", self.log_id, self._identifier)
            try:
                discovered = await discover.android_tvs()
                for item in discovered:
                    if item["name"] == self._name:
                        if self._atv.host != item["address"]:
                            _LOG.info(
                                "[%s] IP address of %s changed: %s", self.log_id, self._identifier, item["address"]
                            )
                            self._atv.host = item["address"]
                            self.events.emit(Events.IP_ADDRESS_CHANGED, self._identifier, self._atv.host)
                            break
            except Exception as e:
                # extra safety, otherwise reconnection task is dead
                _LOG.error("[%s] Discovery failed: %s", self.log_id, e)
        else:
            await asyncio.sleep(backoff)

    def disconnect(self) -> None:
        """Disconnect from Android TV."""
        self._reconnect_delay = MIN_RECONNECT_DELAY
        self._atv.disconnect()
        self._state = DeviceState.DISCONNECTED
        self.events.emit(Events.DISCONNECTED, self._identifier)

    # Callbacks
    def _is_on_updated(self, is_on: bool) -> None:
        """Notify that the Android TV power state is updated."""
        _LOG.info("[%s] is on: %s", self.log_id, is_on)
        update = {}
        if is_on:
            update["state"] = media_player.States.ON.value
        else:
            update["state"] = media_player.States.OFF.value
        self.events.emit(Events.UPDATE, self._identifier, update)

    def _current_app_updated(self, current_app: str) -> None:
        """Notify that the current app on Android TV is updated."""
        _LOG.debug("[%s] current_app: %s", self.log_id, current_app)
        update = {"source": current_app}

        # Priority 1: Use direct ID mappings
        if current_app in apps.IdMappings:
            update["source"] = apps.IdMappings[current_app]

        # Priority 2: Fuzzy name matching
        else:
            for query, app in apps.NameMatching.items():
                if query in current_app:
                    update["source"] = app
                    break

        # Priority 3: Fallback to Google Play lookup
        if self.use_external_metadata:
            try:
                from external_metadata import get_app_name
                update["title"] = get_app_name(current_app)
            except Exception as e:
                _LOG.warning("[%s] Failed to get external metadata: %s", self.log_id, e)

        # TODO verify "idle" apps, probably best to make them configurable
        if current_app in ("com.google.android.tvlauncher", "com.android.systemui"):
            update["state"] = media_player.States.ON.value
            update["title"] = ""
        else:
            update["state"] = media_player.States.PLAYING.value
            update["title"] = update["source"]

        self.events.emit(Events.UPDATE, self._identifier, update)

    def _volume_info_updated(self, volume_info: dict[str, str | bool]) -> None:
        """Notify that the Android TV volume information is updated."""
        _LOG.debug("[%s] volume_info: %s", self.log_id, volume_info)
        update = {"volume": volume_info["level"], "muted": volume_info["muted"]}
        self.events.emit(Events.UPDATE, self._identifier, update)

    def _is_available_updated(self, is_available: bool):
        """Notify that the Android TV is ready to receive commands or is unavailable."""
        _LOG.info("[%s] is_available: %s", self.log_id, is_available)
        self._state = DeviceState.CONNECTED if is_available else DeviceState.CONNECTING
        self.events.emit(Events.CONNECTED if is_available else Events.DISCONNECTED, self.identifier)

    def _update_app_list(self) -> None:
        update = {}
        source_list = []
        for app in apps.Apps:
            source_list.append(app)

        update["source_list"] = source_list
        self.events.emit(Events.UPDATE, self._identifier, update)

    async def send_media_player_command(self, cmd_id: str) -> ucapi.StatusCodes:
        """
        Send a UCR2 media-player entity command to the Android TV.

        :param cmd_id: command identifier
        :return: OK if scheduled to be sent,
                 SERVICE_UNAVAILABLE if there's no connection to the device,
                 BAD_REQUEST if the ``cmd_id`` is unknown or not supported
        """
        if not self._profile:
            _LOG.error("[%s] Cannot send command %s: no device profile set", self.log_id, cmd_id)
            return ucapi.StatusCodes.SERVER_ERROR

        if command := self._profile.command(cmd_id):
            return await self._send_command(command.keycode, command.action)

        _LOG.error("[%s] Cannot send command, unknown or unsupported command: %s", self.log_id, cmd_id)
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
        Select a given source, either a pre-defined app, input or by app-link/id.

        :param source: the friendly source name or an app-link / id
        """
        if source in apps.Apps:
            return await self._launch_app(apps.Apps[source]["url"])
        if source in inputs.KeyCode:
            return await self._switch_input(source)

        return await self._launch_app(source)

    @async_handle_atvlib_errors
    async def _send_command(self, keycode: int | str, action: KeyPress = KeyPress.SHORT) -> ucapi.StatusCodes:
        """
        Send a key press to Android TV.

        This does not block; it buffers the data and arranges for it to be
        sent out asynchronously.

         Error handling is performed in the ``async_handle_atvlib_errors`` wrapper with the following return codes:

         - SERVICE_UNAVAILABLE if there's no connection to the device,
         - BAD_REQUEST if the ``keycode`` is unknown
         - CONFLICT if the connection is not authenticated and requires re-pairing

        :param keycode: int (e.g. 26) or str (e.g. "KEYCODE_POWER" or just "POWER")
                         from the enum RemoteKeyCode in remotemessage.proto. See
                         https://github.com/tronikos/androidtvremote2/blob/v0.0.14/src/androidtvremote2/remotemessage.proto#L90
        :param action: key press action type, default = short press
        :return: OK if scheduled to be sent, other error code in case of an error

        """  # noqa
        if action in (KeyPress.LONG, KeyPress.BEGIN):
            direction = "START_LONG"
        elif action == KeyPress.END:
            direction = "END_LONG"
        else:
            direction = "SHORT"

        self._atv.send_key_command(keycode, direction)

        if action == KeyPress.DOUBLE_CLICK:
            self._atv.send_key_command(keycode, direction)
        elif action == KeyPress.LONG:
            await asyncio.sleep(LONG_PRESS_DELAY)
            self._atv.send_key_command(keycode, "END_LONG")

        return ucapi.StatusCodes.OK

    @async_handle_atvlib_errors
    async def _launch_app(self, app: str) -> ucapi.StatusCodes:
        """Launch an app on Android TV."""
        self._atv.send_launch_app_command(app)
        return ucapi.StatusCodes.OK

    async def _switch_input(self, source: str) -> ucapi.StatusCodes:
        """
        TEST FUNCTION: Send a KEYCODE_TV_INPUT_* key.

        Uses the inputs.py mappings to map from an input name to a KEYCODE_TV_* key.
        """
        if source in inputs.KeyCode:
            return await self._send_command(inputs.KeyCode[source])
        return ucapi.StatusCodes.BAD_REQUEST
