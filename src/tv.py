"""
This module implements the Android TV communication of the Remote Two integration driver.

:copyright: (c) 2023-2024 by Unfolded Circle ApS.
:license: Mozilla Public License Version 2.0, see LICENSE for more details.
"""

# pylint: disable=too-many-lines

import asyncio
import logging
import os
import socket
import time
from asyncio import AbstractEventLoop, timeout
from enum import IntEnum
from functools import wraps
from typing import Any, Awaitable, Callable, Concatenate, Coroutine, ParamSpec, TypeVar

import pychromecast
import ucapi
from androidtvremote2 import (
    AndroidTVRemote,
    CannotConnect,
    ConnectionClosed,
    InvalidAuth,
)
from pychromecast import CastStatus, CastStatusListener, Chromecast, RequestTimeout
from pychromecast.controllers.media import (
    MEDIA_PLAYER_STATE_BUFFERING,
    MEDIA_PLAYER_STATE_IDLE,
    MEDIA_PLAYER_STATE_PAUSED,
    MEDIA_PLAYER_STATE_PLAYING,
    MEDIA_PLAYER_STATE_UNKNOWN,
    METADATA_TYPE_GENERIC,
    METADATA_TYPE_MOVIE,
    METADATA_TYPE_MUSICTRACK,
    METADATA_TYPE_TVSHOW,
    MediaStatus,
    MediaStatusListener,
)
from pychromecast.error import PyChromecastError
from pychromecast.socket_client import ConnectionStatus, ConnectionStatusListener
from pyee.asyncio import AsyncIOEventEmitter
from ucapi import media_player
from ucapi.media_player import Attributes as MediaAttr
from ucapi.media_player import MediaType

import apps
import discover
import inputs
from config import AtvDevice
from external_metadata import encode_icon_to_data_uri, get_app_metadata
from profiles import KeyPress, Profile
from util import filter_data_img_properties

_LOG = logging.getLogger(__name__)

CONNECTION_TIMEOUT: float = 10.0
"""Android TV device connection timeout in seconds."""
BACKOFF_MAX: int = 30
"""Maximum backoff duration in seconds."""
MIN_RECONNECT_DELAY: float = 0.5
BACKOFF_FACTOR: float = 1.5

LONG_PRESS_DELAY: float = 0.8

HOMESCREEN_IMAGE = None


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


GOOGLE_CAST_MEDIA_TYPES_MAP = {
    METADATA_TYPE_GENERIC: MediaType.VIDEO,
    METADATA_TYPE_MOVIE: MediaType.MOVIE,
    METADATA_TYPE_MUSICTRACK: MediaType.MUSIC,
    METADATA_TYPE_TVSHOW: MediaType.TVSHOW,
}

GOOGLE_CAST_MEDIA_STATES_MAP = {
    MEDIA_PLAYER_STATE_UNKNOWN: media_player.States.ON,
    MEDIA_PLAYER_STATE_IDLE: media_player.States.PLAYING,
    MEDIA_PLAYER_STATE_BUFFERING: media_player.States.BUFFERING,
    MEDIA_PLAYER_STATE_PAUSED: media_player.States.PAUSED,
    MEDIA_PLAYER_STATE_PLAYING: media_player.States.PLAYING,
}

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
            # pylint: disable=W0212
            self._loop.create_task(self.connect())
            return ucapi.StatusCodes.SERVICE_UNAVAILABLE
        except InvalidAuth as ex:
            _LOG.error("[%s] Cannot send command: %s", self.log_id, ex)
            return ucapi.StatusCodes.CONFLICT
        except ValueError as ex:
            _LOG.error("[%s] Cannot send command, invalid key_code: %s", self.log_id, ex)
            return ucapi.StatusCodes.BAD_REQUEST

    return wrapper


# pylint: disable=too-many-public-methods
class AndroidTv(CastStatusListener, MediaStatusListener, ConnectionStatusListener):
    """Representing an Android TV device."""

    # pylint: disable=R0917
    def __init__(
        self,
        certfile: str,
        keyfile: str,
        device_config: AtvDevice,
        profile: Profile | None = None,
        loop: AbstractEventLoop | None = None,
    ):
        """
        Create instance with given IP address of Android TV device.

        :param certfile: filename that contains the client certificate in PEM format.
        :param keyfile: filename that contains the public key in PEM format.
        :param device_config: device configuration of the Android TV.
        :param profile: Device profile used for command mappings.
        :param loop: event loop. Used for connections and futures.
        """
        self._device_config = device_config
        self._state: DeviceState = DeviceState.IDLE
        self._name: str = device_config.name
        self._loop: AbstractEventLoop = loop or asyncio.get_running_loop()
        self.events = AsyncIOEventEmitter(self._loop)

        name = os.getenv("UC_CLIENT_NAME", socket.gethostname().split(".", 1)[0])
        self._atv: AndroidTVRemote = AndroidTVRemote(
            client_name=name,
            certfile=certfile,
            keyfile=keyfile,
            host=device_config.address,
            loop=self._loop,
        )
        self._identifier: str | None = device_config.id
        self._profile: Profile | None = profile
        self._connection_attempts: int = 0
        self._reconnect_delay: float = MIN_RECONNECT_DELAY

        # Hook up callbacks
        self._atv.add_is_on_updated_callback(self._is_on_updated)
        self._atv.add_current_app_updated_callback(self._current_app_updated)
        self._atv.add_volume_info_updated_callback(self._volume_info_updated)
        self._atv.add_is_available_updated_callback(self._is_available_updated)
        self._chromecast: Chromecast | None = None
        self._media_title: str | None = None
        self._media_app: str | None = None
        self._media_album: str | None = None
        self._media_artist: str | None = None
        self._media_position = 0
        self._media_duration = 0
        self._last_update_position_time: float = 0
        self._media_type = METADATA_TYPE_MOVIE
        self._media_image_url: str | None = None
        self._app_image_url: str = ""
        self._use_app_url = not device_config.use_chromecast
        self._player_state = media_player.States.ON
        self._muted = False

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

    @property
    def device_config(self) -> AtvDevice:
        """Return current device configuration."""
        return self._device_config

    @property
    def media_title(self) -> str | None:
        """Return media title."""
        if self._media_title and self._media_title != "":
            return self._media_title
        if self._media_app in apps.IdMappings:
            return apps.IdMappings[self._media_app]
        return self._media_app

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

    # pylint: disable=too-many-statements,too-many-return-statements,too-many-branches
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
            _LOG.error(
                "[%s] Invalid authentication for %s while reconnecting",
                self.log_id,
                self._identifier,
            )
            self.events.emit(Events.AUTH_ERROR, self._identifier)

        self._atv.keep_reconnecting(_handle_invalid_auth)

        device_info = self._atv.device_info
        _LOG.info("[%s] Device information: %s", self.log_id, device_info)

        self._update_app_list()
        self._state = DeviceState.CONNECTED
        self.events.emit(Events.CONNECTED, self._identifier)

        # Connect to Chromecast if supported
        self._chromecast_connect()
        return True

    def _chromecast_connect(self):
        if self._device_config.use_chromecast:
            try:
                if self._chromecast:
                    self._chromecast.disconnect(timeout=0)
                    self._chromecast = None
            except Exception:
                pass
            self._chromecast = pychromecast.get_chromecast_from_host(
                host=(self._atv.host, None, None, None, None),
                tries=10,
                timeout=CONNECTION_TIMEOUT,
                retry_wait=CONNECTION_TIMEOUT,
            )

            try:
                self._chromecast.register_status_listener(self)
                self._chromecast.socket_client.media_controller.register_status_listener(self)
                self._chromecast.register_connection_listener(self)
                _LOG.info("[%s] Chromecast connecting", self.log_id)
                self._chromecast.wait(timeout=CONNECTION_TIMEOUT)
                _LOG.info("[%s] Chromecast connected", self.log_id)
            except (RequestTimeout, RuntimeError):
                _LOG.info(
                    "[%s] Device is not active or Chromecast is not supported on this devices",
                    self.log_id,
                )

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
            _LOG.debug(
                "[%s] Start resolving IP address for %s...",
                self.log_id,
                self._identifier,
            )
            try:
                discovered = await discover.android_tvs()
                for item in discovered:
                    if item["name"] == self._name:
                        if self._atv.host != item["address"]:
                            _LOG.info(
                                "[%s] IP address of %s changed: %s",
                                self.log_id,
                                self._identifier,
                                item["address"],
                            )
                            self._atv.host = item["address"]
                            self.events.emit(
                                Events.IP_ADDRESS_CHANGED,
                                self._identifier,
                                self._atv.host,
                            )
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
        if self._chromecast and self._chromecast.socket_client.is_alive():
            try:
                self._chromecast.disconnect(timeout=0)
            except Exception:
                pass
        self._state = DeviceState.DISCONNECTED
        self.events.emit(Events.DISCONNECTED, self._identifier)

    # Callbacks
    async def _apply_current_app_metadata(self, current_app: str) -> dict:
        global HOMESCREEN_IMAGE

        update = {}
        # one-time initialization
        if HOMESCREEN_IMAGE is None:
            HOMESCREEN_IMAGE = ""
            HOMESCREEN_IMAGE = await encode_icon_to_data_uri("config://androidtv.png")

        # Special handling for homescreen & Android TV system apps: show pre-defined icon
        homescreen_app = apps.is_homescreen_app(current_app)
        if homescreen_app or apps.is_standby_app(current_app):
            update[MediaAttr.SOURCE] = apps.IdMappings[current_app]
            update[MediaAttr.MEDIA_TITLE] = ""
            update[MediaAttr.MEDIA_IMAGE_URL] = HOMESCREEN_IMAGE
            update[MediaAttr.STATE] = (
                media_player.States.ON.value if homescreen_app else media_player.States.STANDBY.value
            )
            return update

        # Track state of data sources
        offline_name = None
        offline_match = None
        external_name = None
        external_icon = None

        # Try offline ID mapping first
        if current_app in apps.IdMappings:
            offline_name = apps.IdMappings[current_app]
            self._media_app = offline_name

        # Try fuzzy offline name matching if ID mapping failed
        if not offline_name:
            for query, name in apps.NameMatching.items():
                if query in current_app:
                    offline_match = name
                    self._media_app = name
                    break

        # Try external metadata
        metadata = (
            await get_app_metadata(current_app) if current_app and self._device_config.use_external_metadata else None
        )
        if metadata:
            if _LOG.isEnabledFor(logging.DEBUG):
                _LOG.debug("App metadata: %s", filter_data_img_properties(metadata))
            external_name = metadata.get("name")
            external_icon = metadata.get("icon")
            if external_name:
                self._media_app = external_name
            if external_icon:
                self._app_image_url = external_icon

        # Determine final name/title to use
        name_to_use = offline_name or offline_match or external_name or current_app
        # TODO why set name to both source & media title fields?
        update[MediaAttr.SOURCE] = name_to_use
        if not self._media_title and not self._media_image_url:
            update[MediaAttr.MEDIA_TITLE] = name_to_use

        # Determine which icon to use
        icon_to_use = None
        if self._device_config.use_external_metadata or self._use_app_url:
            if external_icon:
                icon_to_use = external_icon
            else:
                icon_to_use = ""
        elif self._media_image_url:
            # TODO what's the intended logic?
            # `icon_to_use` is never used because of the inverse `if not self._media_image_url:` check below!
            icon_to_use = self._media_image_url

        update[MediaAttr.STATE] = media_player.States.PLAYING.value
        # Skip applying app icon if media image from cast is present
        if not self._media_image_url:
            if not icon_to_use:
                update[MediaAttr.MEDIA_IMAGE_URL] = HOMESCREEN_IMAGE
            else:
                update[MediaAttr.MEDIA_IMAGE_URL] = icon_to_use

        return update

    def _is_on_updated(self, is_on: bool) -> None:
        """Notify that the Android TV power state is updated."""
        asyncio.create_task(self._handle_is_on_updated(is_on))

    async def _handle_is_on_updated(self, is_on: bool):
        _LOG.info("[%s] is on: %s", self.log_id, is_on)
        current_app = self._atv.current_app or ""
        if is_on:
            self._chromecast_connect()
            update = await self._apply_current_app_metadata(current_app)
            update[MediaAttr.STATE] = media_player.States.ON.value
        else:
            update = await self._apply_current_app_metadata(current_app)
            update[MediaAttr.STATE] = media_player.States.OFF.value

        self.events.emit(Events.UPDATE, self._identifier, update)

    def _current_app_updated(self, current_app: str) -> None:
        """Notify that the current app on Android TV is updated."""
        asyncio.create_task(self._handle_current_app_updated(current_app))

    async def _handle_current_app_updated(self, current_app: str):
        _LOG.debug("[%s] current_app: %s", self.log_id, current_app)
        update = await self._apply_current_app_metadata(current_app)
        self.events.emit(Events.UPDATE, self._identifier, update)

    def _volume_info_updated(self, volume_info: dict[str, str | bool]) -> None:
        """Notify that the Android TV volume information is updated."""
        _LOG.debug("[%s] volume_info: %s", self.log_id, volume_info)
        self._muted = volume_info["muted"]
        update = {MediaAttr.VOLUME: volume_info["level"], MediaAttr.MUTED: self._muted}
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

        update[MediaAttr.SOURCE_LIST] = source_list
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
            _LOG.error(
                "[%s] Cannot send command %s: no device profile set",
                self.log_id,
                cmd_id,
            )
            return ucapi.StatusCodes.SERVER_ERROR

        if command := self._profile.command(cmd_id):
            return await self._send_command(command.keycode, command.action)

        _LOG.error(
            "[%s] Cannot send command, unknown or unsupported command: %s",
            self.log_id,
            cmd_id,
        )
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

    def new_connection_status(self, status: ConnectionStatus) -> None:
        """Receive new connection status event from Google cast."""
        _LOG.info("[%s] Received Chromecast connection status : %s", self.log_id, status)

    def new_media_status(self, status: MediaStatus) -> None:
        """Receive new media status event from Google cast."""
        if not self._loop or not self._loop.is_running():
            _LOG.warning("[%s] No running event loop for handling new media status", self.log_id)
            return

        try:
            asyncio.run_coroutine_threadsafe(self._handle_new_media_status(status), self._loop)
        except Exception as e:
            _LOG.error("[%s] Failed to schedule media status handler: %s", self.log_id, e)

    async def _handle_new_media_status(self, status: MediaStatus):
        update = {}

        if (
            status.player_state
            and GOOGLE_CAST_MEDIA_STATES_MAP.get(status.player_state, media_player.States.PLAYING) != self._player_state
        ):
            # PLAYING, PAUSED, IDLE
            self._player_state = GOOGLE_CAST_MEDIA_STATES_MAP.get(status.player_state, media_player.States.PLAYING)
            self._last_update_position_time = 0
            update[MediaAttr.STATE] = self._player_state

        if status.album_name != self._media_album:
            self._media_album = status.album_name or ""
            update[MediaAttr.MEDIA_ALBUM] = self._media_album

        if status.artist != self._media_artist:
            self._media_artist = status.artist or ""
            update[MediaAttr.MEDIA_ARTIST] = self._media_artist

        if status.title != self._media_title:
            current_title = self.media_title
            self._media_title = status.title or ""
            if current_title != self.media_title:
                _LOG.debug("[%s] Chromecast Media info updated : %s", self.log_id, status)
                update[MediaAttr.MEDIA_TITLE] = self.media_title

        current_time = int(status.current_time) if status.current_time else 0
        duration = int(status.duration) if status.duration else 0
        changed_duration = False

        if duration != self._media_duration:
            self._media_duration = duration
            update[MediaAttr.MEDIA_DURATION] = self._media_duration
            changed_duration = True

        # Update position every 30 seconds
        if changed_duration or (
            current_time != self._media_position and self._last_update_position_time + 30 < time.time()
        ):
            self._media_position = current_time
            update[MediaAttr.MEDIA_POSITION] = self._media_position
            update[MediaAttr.MEDIA_DURATION] = self._media_duration
            self._last_update_position_time = time.time()

        if (
            status.metadata_type
            and GOOGLE_CAST_MEDIA_TYPES_MAP.get(status.metadata_type, MediaType.VIDEO) != self._media_type
        ):
            self._media_type = GOOGLE_CAST_MEDIA_TYPES_MAP.get(status.metadata_type, MediaType.VIDEO)
            update[MediaAttr.MEDIA_TYPE] = self._media_type

        if status.images and len(status.images) > 0 and status.images[0].url != self._media_image_url:
            self._media_image_url = status.images[0].url
            update[MediaAttr.MEDIA_IMAGE_URL] = self._media_image_url
            self._use_app_url = False
        else:
            self._media_image_url = None
            if self._device_config.use_external_metadata:
                self._use_app_url = True
                if self._app_image_url:
                    update[MediaAttr.MEDIA_IMAGE_URL] = await encode_icon_to_data_uri(self._app_image_url)

        if update:
            if _LOG.isEnabledFor(logging.DEBUG):
                _LOG.debug(
                    "[%s] Update remote with Chromecast info : %s", self.log_id, filter_data_img_properties(update)
                )

            self.events.emit(Events.UPDATE, self._identifier, update)

    def load_media_failed(self, queue_item_id: int, error_code: int) -> None:
        """Receive new media failed event from Google cast."""

    def new_cast_status(self, status: CastStatus) -> None:
        """Receive new cast event from Google cast."""
        _LOG.debug("[%s] Received Chromecast cast status : %s", self.log_id, status)

        if not self._loop or not self._loop.is_running():
            _LOG.warning("[%s] No running event loop for cast status", self.log_id)
            return

        try:
            asyncio.run_coroutine_threadsafe(self._handle_new_cast_status(status), self._loop)
        except Exception as e:
            _LOG.error("[%s] Failed to schedule cast status handler: %s", self.log_id, e)

    async def _handle_new_cast_status(self, status: CastStatus):
        current_title = self.media_title
        if status.display_name:
            self._media_app = status.display_name

        if current_title != self.media_title:
            update = {MediaAttr.MEDIA_TITLE: self.media_title}
            _LOG.debug("[%s] Update remote with Chromecast info : %s", self.log_id, update)
            self.events.emit(Events.UPDATE, self._identifier, update)

    async def media_seek(self, position: float) -> ucapi.StatusCodes:
        """Seek the media at the given position using Google Cast."""
        try:
            if self._chromecast is None:
                return ucapi.StatusCodes.SERVICE_UNAVAILABLE
            self._chromecast.media_controller.seek(position, timeout=CONNECTION_TIMEOUT)
            return ucapi.StatusCodes.OK
        except Exception as ex:
            _LOG.error("[%s] Chromecast error seeking command : %s", self.log_id, ex)
        return ucapi.StatusCodes.SERVER_ERROR

    async def volume_up(self) -> ucapi.StatusCodes:
        """Change volume up. Use Google Cast volume control if enabled, otherwise use Android TV volume control."""
        if self.device_config.use_chromecast and self.device_config.use_chromecast_volume and self._chromecast:
            # if chromecast is not connected, use default volume control. Google cast sometimes disconnects!
            try:
                _LOG.debug(
                    "[%s] Volume up : current %.2f + step %s",
                    self.log_id,
                    self._chromecast.status.volume_level,
                    self._device_config.volume_step / 100,
                )
                self._chromecast.volume_up(delta=float(self._device_config.volume_step / 100))
                return ucapi.StatusCodes.OK
            except PyChromecastError as ex:
                _LOG.error("[%s] Chromecast error sending command : %s", self.log_id, ex)
                return ucapi.StatusCodes.SERVER_ERROR
        return await self.send_media_player_command(media_player.Commands.VOLUME_UP)

    async def volume_down(self) -> ucapi.StatusCodes:
        """Change volume down. Use Google Cast volume control if enabled, otherwise use Android TV volume control."""
        if self.device_config.use_chromecast and self.device_config.use_chromecast_volume and self._chromecast:
            # if chromecast is not connected, use default volume control. Google cast sometimes disconnects!
            try:
                _LOG.debug(
                    "[%s] Volume down : current %.2f - step %s",
                    self.log_id,
                    self._chromecast.status.volume_level,
                    self._device_config.volume_step / 100,
                )
                self._chromecast.volume_down(delta=float(self._device_config.volume_step / 100))
                return ucapi.StatusCodes.OK
            except PyChromecastError as ex:
                _LOG.error("[%s] Chromecast error sending command : %s", self.log_id, ex)
                return ucapi.StatusCodes.SERVER_ERROR
        return await self.send_media_player_command(media_player.Commands.VOLUME_DOWN)

    async def volume_mute_toggle(self) -> ucapi.StatusCodes:
        """Mute toggle. Use Google Cast volume control if enabled, otherwise use Android TV volume control."""
        if self.device_config.use_chromecast and self.device_config.use_chromecast_volume and self._chromecast:
            try:
                self._muted = not self._muted
                _LOG.debug("[%s] Mute toggle : %s", self.log_id, self._muted)
                self._chromecast.set_volume_muted(self._muted)
                return ucapi.StatusCodes.OK
            except PyChromecastError as ex:
                _LOG.error("[%s] Chromecast error sending command : %s", self.log_id, ex)
                return ucapi.StatusCodes.SERVER_ERROR
        return await self.send_media_player_command(media_player.Commands.MUTE_TOGGLE)

    async def volume_set(self, volume: int | None) -> ucapi.StatusCodes:
        """Set volume using Google Cast."""
        if self._chromecast is None:
            return ucapi.StatusCodes.SERVICE_UNAVAILABLE
        if volume is None or volume < 0 or volume > 100:
            return ucapi.StatusCodes.BAD_REQUEST
        try:
            _LOG.debug("[%s] Set volume : %.2f", self.log_id, float(volume) / 100)
            self._chromecast.set_volume(float(volume) / 100)
            return ucapi.StatusCodes.OK
        except PyChromecastError as ex:
            _LOG.error("[%s] Chromecast error sending command : %s", self.log_id, ex)
        return ucapi.StatusCodes.SERVER_ERROR
