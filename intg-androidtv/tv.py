import asyncio
import logging

from enum import IntEnum

from pyee import AsyncIOEventEmitter

from androidtvremote2 import (
    AndroidTVRemote,
    CannotConnect,
    ConnectionClosed,
    InvalidAuth,
)

import apps

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

    def __init__(self, loop: any, data_path: str):
        self._loop = loop
        self._data_path = data_path
        self.events = AsyncIOEventEmitter(self._loop)
        self._atv: AndroidTVRemote | None = None
        self.identifier = None
        self.name = None
        self.mac = None
        self.address = None
        self._connection_attempts = 0

    async def init(self, host: str, name: str = "") -> bool:
        self._atv = AndroidTVRemote(
            client_name="Remote Two",
            certfile=self._data_path + "/androidtv_remote_cert.pem",
            keyfile=self._data_path + "/androidtv_remote_key.pem",
            host=host,
            loop=self._loop,
        )

        if await self._atv.async_generate_cert_if_missing():
            LOG.debug("Generated new certificate")

        success = False

        while not success:
            try:
                self.name, self.mac = await self._atv.async_get_name_and_mac()
                success = True
                self._connection_attempts = 0
            except (CannotConnect, ConnectionClosed):
                self._connection_attempts += 1
                backoff = self.backoff()
                LOG.error("Cannot connect, trying again in %ss", backoff)
                await asyncio.sleep(backoff)

        if name != "":
            self.name = name

        self.identifier = self.mac.replace(":", "")
        self.address = host

        # Hook up callbacks
        self._atv.add_is_on_updated_callback(self.is_on_updated)
        self._atv.add_current_app_updated_callback(self.current_app_updated)
        self._atv.add_volume_info_updated_callback(self.volume_info_updated)
        self._atv.add_is_available_updated_callback(self.is_available_updated)

        LOG.debug("Android TV initialised: %s, %s", self.identifier, self.name)
        return True

    def backoff(self) -> int:
        if self._connection_attempts * BACKOFF_SEC >= BACKOFF_MAX:
            return BACKOFF_MAX
        return self._connection_attempts * BACKOFF_SEC

    async def start_pairing(self) -> None:
        await self._atv.async_start_pairing()

    async def finish_pairing(self, pin: str) -> bool:
        try:
            await self._atv.async_finish_pairing(pin)
            return True
        except InvalidAuth as exc:
            LOG.error("Invalid pairing code. Error: %s", exc)
            return False
        except ConnectionClosed as exc:
            LOG.error("Initialize pair again. Error: %s", exc)
            return False

    async def connect(self) -> None:
        LOG.debug("Android TV connecting: %s", self.identifier)

        success = False

        while not success:
            try:
                await self._atv.async_connect()
                success = True
                self._connection_attempts = 0
            except InvalidAuth:
                # TODO: In this case we need to re-authenticate
                # How to handle this?
                LOG.error("Invalid auth: %s", self.identifier)
                self.events.emit(Events.ERROR, self.identifier)
                break
            except (CannotConnect, ConnectionClosed):
                LOG.error("Android TV device is unreachable on network: %s", self.identifier)
                self._connection_attempts += 1
                backoff = self.backoff()
                LOG.debug("Trying again in %s", backoff)
                await asyncio.sleep(backoff)

        if not success:
            return

        self._atv.keep_reconnecting()

        self._update_app_list()

        self.events.emit(Events.CONNECTED, self.identifier)

    def disconnect(self) -> None:
        self._atv.disconnect()
        self.events.emit(Events.DISCONNECTED, self.identifier)

    # Callbacks
    def is_on_updated(self, is_on):
        LOG.info("Device is on: %s", is_on)
        update = {}
        if is_on:
            update["state"] = "ON"
        else:
            update["state"] = "OFF"
        self.events.emit(Events.UPDATE, self.identifier, update)

    def current_app_updated(self, current_app):
        LOG.info("Notified that current_app: %s", current_app)
        update = {}

        if "netflix" in current_app:
            update["source"] = "Netflix"
        elif "youtube" in current_app:
            update["source"] = "Youtube"
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

        if current_app == "com.google.android.tvlauncher":
            update["state"] = "ON"
            update["title"] = ""
        else:
            update["state"] = "PLAYING"
            update["title"] = update["source"]

        self.events.emit(Events.UPDATE, self.identifier, update)

    def volume_info_updated(self, volume_info):
        LOG.info("Notified that volume_info: %s", volume_info)
        update = {"volume": volume_info["level"], "muted": volume_info["muted"]}
        self.events.emit(Events.UPDATE, self.identifier, update)

    def is_available_updated(self, is_available):
        LOG.info("Notified that is_available: %s", is_available)
        # if is_available is False:
        #     self.events.emit(EVENTS.DISCONNECTED, self.identifier)

    def _update_app_list(self) -> None:
        update = {}
        source_list = []
        for app in apps.Apps:
            source_list.append(app)

        update["source_list"] = source_list
        self.events.emit(Events.UPDATE, self.identifier, update)

    # Commands
    def _send_command(self, key_code: str, direction: str = "SHORT") -> bool:
        try:
            self._atv.send_key_command(key_code, direction)
            return True
        except ConnectionClosed:
            LOG.error("Cannot send command, connection lost: %s", self.identifier)
            return False

    def turn_on(self) -> bool:
        return self._send_command("POWER")

    def turn_off(self) -> bool:
        return self._send_command("POWER")

    def play_pause(self) -> bool:
        return self._send_command("MEDIA_PLAY_PAUSE")

    def next(self) -> bool:
        return self._send_command("MEDIA_NEXT")

    def previous(self) -> bool:
        return self._send_command("MEDIA_PREVIOUS")

    def volume_up(self) -> bool:
        return self._send_command("VOLUME_UP")

    def volume_down(self) -> bool:
        return self._send_command("VOLUME_DOWN")

    def mute_toggle(self) -> bool:
        return self._send_command("VOLUME_MUTE")

    def cursor_up(self) -> bool:
        return self._send_command("DPAD_UP")

    def cursor_down(self) -> bool:
        return self._send_command("DPAD_DOWN")

    def cursor_left(self) -> bool:
        return self._send_command("DPAD_LEFT")

    def cursor_right(self) -> bool:
        return self._send_command("DPAD_RIGHT")

    def cursor_enter(self) -> bool:
        return self._send_command("DPAD_CENTER")

    def home(self) -> bool:
        return self._send_command("HOME")

    def back(self) -> bool:
        return self._send_command("BACK")

    def channel_up(self) -> bool:
        return self._send_command("CHANNEL_UP")

    def channel_down(self) -> bool:
        return self._send_command("CHANNEL_DOWN")

    def launch_app(self, app) -> bool:
        try:
            self._atv.send_launch_app_command(apps.Apps[app]["url"])
            return True
        except ConnectionClosed:
            LOG.error("Cannot send command, connection lost: %s", self.identifier)
            return False
