import asyncio
import base64
import logging
import random

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
LOG.setLevel(logging.DEBUG)

BACKOFF_MAX = 30
BACKOFF_SEC = 2

class EVENTS(IntEnum):
    CONNECTING = 0
    CONNECTED = 1
    DISCONNECTED = 2
    PAIRED = 3
    ERROR = 4
    UPDATE = 5
    VOLUME_CHANGED = 6


class AndroidTv(object):
    def __init__(self, loop: any, dataPath: str):
        self._loop = loop
        self._dataPath = dataPath
        self.events = AsyncIOEventEmitter(self._loop)
        self._atv = None
        self.identifier = None
        self.name = None
        self.mac = None
        self.address = None
        self._connectionAttempts = 0

    async def init(self, host: str, name: str = "") -> bool:
        self._atv = AndroidTVRemote(
            client_name="Remote Two",
            certfile=self._dataPath + '/androidtv_remote_cert.pem',
            keyfile=self._dataPath + '/androidtv_remote_key.pem',
            host = host,
            loop = self._loop
        )

        if await self._atv.async_generate_cert_if_missing():
            LOG.debug("Generated new certificate")

        success = False

        while success == False:
            try:
                self.name, self.mac = await self._atv.async_get_name_and_mac()
                success = True
                self._connectionAttempts = 0
            except (CannotConnect, ConnectionClosed):
                self._connectionAttempts += 1
                backoff = self.backoff()
                LOG.error('Cannot connect, trying again in %ss', backoff)
                await asyncio.sleep(backoff)

        if name != "":
            self.name = name

        self.identifier = self.mac.replace(':','')
        self.address = host

        LOG.debug('Android TV initialised: %s, %s', self.identifier, self.name)
        return True


    def backoff(self) -> int:
        if self._connectionAttempts * BACKOFF_SEC == BACKOFF_MAX:
            return BACKOFF_MAX
        return self._connectionAttempts * BACKOFF_SEC
    
    async def startPairing(self) -> None:
        await self._atv.async_start_pairing()
    

    async def finishPairing(self, pin: str) -> bool:
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
        LOG.debug('Android TV connecting: %s', self.identifier)

        try:
            await self._atv.async_connect()
        except InvalidAuth:
            LOG.error('Invalid auth: %s', self.identifier)
            self.events.emit(EVENTS.ERROR, self.identifier)
        except (CannotConnect, ConnectionClosed, asyncio.TimeoutError):
            LOG.error('Android TV device is unreachable on network: %s', self.identifier)
            self.events.emit(EVENTS.ERROR, self.identifier)

        self._atv.keep_reconnecting()

        # Hook up callbacks
        self._atv.add_is_on_updated_callback(self.is_on_updated)
        self._atv.add_current_app_updated_callback(self.current_app_updated)
        self._atv.add_volume_info_updated_callback(self.volume_info_updated)
        self._atv.add_is_available_updated_callback(self.is_available_updated)

        self._updateAppList()

        self.events.emit(EVENTS.CONNECTED, self.identifier)

    def disconnect(self) -> None:
        self._atv.disconnect()
        self.events.emit(EVENTS.DISCONNECTED, self.identifier)

    # Callbacks
    def is_on_updated(self, is_on):
        LOG.info('Device is on: %s', is_on)
        update = {}
        if is_on:
            update['state'] = 'ON'
        else:
            update['state'] = 'OFF'
        self.events.emit(EVENTS.UPDATE, update)

    def current_app_updated(self, current_app):
        LOG.info("Notified that current_app: %s", current_app)
        update = {}

        if 'netflix' in current_app:
            update['source'] = 'Netflix'
        elif 'youtube' in current_app:
            update['source'] = 'Youtube'
        elif 'amazonvideo' in current_app:
            update['source'] = 'Prime Video'
        elif 'hbomax' in current_app:
            update['source'] = 'HBO Max'
        elif 'disney' in current_app:
            update['source'] = 'Disney+'
        elif 'apple' in current_app:
            update['source'] = 'Apple TV'
        elif 'plex' in current_app:
            update['source'] = 'Plex'
        elif 'kodi' in current_app:
            update['source'] = 'Kodi'
        elif 'emby' in current_app:
            update['source'] = 'Emby'
        else:
            update['source'] = current_app

        if current_app == 'com.google.android.tvlauncher':
            update['state'] = 'ON'
            update['title'] = ''
        else:
            update['state'] = 'PLAYING'
            update['title'] = update['source']

        self.events.emit(EVENTS.UPDATE, update)

    def volume_info_updated(self, volume_info):
        LOG.info("Notified that volume_info: %s", volume_info)
        update = {}
        update['volume'] = volume_info['level']
        update['muted'] = volume_info['muted']
        self.events.emit(EVENTS.UPDATE, update)

    def is_available_updated(self, is_available):
        LOG.info("Notified that is_available: %s", is_available)
        if is_available is False:
            self.events.emit(EVENTS.DISCONNECTED, self.identifier)

    def _updateAppList(self) -> None:
        update = {}
        list = []
        for app in apps.Apps:
            list.append(app)

        update['source_list'] = list
        self.events.emit(EVENTS.UPDATE, update)

    # Commands
    def _sendCommand(self, keyCode: str, direction: str = "SHORT") -> bool:
        try:
            self._atv.send_key_command(keyCode, direction)
            return True
        except ConnectionClosed:
            LOG.error('Cannot send command, connection lost: %s', self.identifier)
            return False
        
    def turnOn(self) -> bool:
        return self._sendCommand('POWER')
    
    def turnOff(self) -> bool:
        return self._sendCommand('POWER')
    
    def playPause(self) -> bool:
        return self._sendCommand('MEDIA_PLAY_PAUSE')
    
    def next(self) -> bool:
        return self._sendCommand('MEDIA_NEXT')
    
    def previous(self) -> bool:
        return self._sendCommand('MEDIA_PREVIOUS')
    
    def volumeUp(self) -> bool:
        return self._sendCommand('VOLUME_UP')
    
    def volumeDown(self) -> bool:
        return self._sendCommand('VOLUME_DOWN')
    
    def muteToggle(self) -> bool:
        return self._sendCommand('VOLUME_MUTE')
    
    def cursorUp(self) -> bool:
        return self._sendCommand('DPAD_UP')
    
    def cursorDown(self) -> bool:
        return self._sendCommand('DPAD_DOWN')
    
    def cursorLeft(self) -> bool:
        return self._sendCommand('DPAD_LEFT')
    
    def cursorRight(self) -> bool:
        return self._sendCommand('DPAD_RIGHT')
    
    def cursorEnter(self) -> bool:
        return self._sendCommand('DPAD_CENTER')
    
    def home(self) -> bool:
        return self._sendCommand('HOME')
    
    def back(self) -> bool:
        return self._sendCommand('BACK')
    
    def channelUp(self) -> bool:
        return self._sendCommand('CHANNEL_UP')
    
    def channelDown(self) -> bool:
        return self._sendCommand('CHANNEL_DOWN')
    
    def launchApp(self, app) -> bool:
        try:
            self._atv.send_launch_app_command(apps.Apps[app]['url'])
            return True
        except ConnectionClosed:
            LOG.error('Cannot send command, connection lost: %s', self.identifier)
            return False