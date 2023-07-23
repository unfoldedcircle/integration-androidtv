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
    def __init__(self, loop, dataPath):
        self._loop = loop
        self._dataPath = dataPath
        self.events = AsyncIOEventEmitter(self._loop)
        self._atv = None
        self.identifier = None
        self.name = None
        self.mac = None
        self.address = None

    async def init(self, host, name = ""):
        self._atv = AndroidTVRemote(
            client_name="Remote Two",
            certfile=self._dataPath + '/androidtv_remote_cert.pem',
            keyfile=self._dataPath + '/androidtv_remote_key.pem',
            host = host,
            loop = self._loop
        )

        if await self._atv.async_generate_cert_if_missing():
            LOG.debug("Generated new certificate")

        try:
            self.name, self.mac = await self._atv.async_get_name_and_mac()
        except (CannotConnect, ConnectionClosed):
            LOG.error('Cannot connect')
            return  False

        if name != "":
            self.name = name

        self.identifier = self.mac.replace(':','')
        self.address = host

        LOG.debug('Android TV initialised: %s, %s', self.identifier, self.name)
        return True


    def backoff(self):
        return self._connectionAttempts * BACKOFF_SEC
    
    async def startPairing(self):
        await self._atv.async_start_pairing()
    

    async def finishPairing(self, pin):
        try:
            await self._atv.async_finish_pairing(pin)
            return True
        except InvalidAuth as exc:
            LOG.error("Invalid pairing code. Error: %s", exc)
            return False
        except ConnectionClosed as exc:
            LOG.error("Initialize pair again. Error: %s", exc)
            return False

    async def connect(self):
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

    def disconnect(self):
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
        update['source'] = current_app
        self.events.emit(EVENTS.UPDATE, update)

    def volume_info_updated(self, volume_info):
        LOG.info("Notified that volume_info: %s", volume_info)

    def is_available_updated(self, is_available):
        LOG.info("Notified that is_available: %s", is_available)
        if is_available is False:
            self.events.emit(EVENTS.DISCONNECTED, self.identifier)
            _ = asyncio.ensure_future(self.connect())

    def _updateAppList(self):
        update = {}
        list = []
        for app in apps.Apps:
            list.append(app)

        update['source_list'] = list
        self.events.emit(EVENTS.UPDATE, update)

    # Commands
    def _sendCommand(self, keyCode, direction = "SHORT"):
        try:
            self._atv.send_key_command(keyCode, direction)
            return True
        except ConnectionClosed:
            LOG.error('Cannot send command, connection lost: %s', self.identifier)
            return False
        
    def turnOn(self):
        return self._sendCommand('POWER')
    
    def turnOff(self):
        return self._sendCommand('POWER')
    
    def playPause(self):
        return self._sendCommand('MEDIA_PLAY_PAUSE')
    
    def next(self):
        return self._sendCommand('MEDIA_NEXT')
    
    def previous(self):
        return self._sendCommand('MEDIA_PREVIOUS')
    
    def volumeUp(self):
        return self._sendCommand('VOLUME_UP')
    
    def volumeDown(self):
        return self._sendCommand('VOLUME_DOWN')
    
    def muteToggle(self):
        return self._sendCommand('VOLUME_MUTE')
    
    def cursorUp(self):
        return self._sendCommand('DPAD_UP')
    
    def cursorDown(self):
        return self._sendCommand('DPAD_DOWN')
    
    def cursorLeft(self):
        return self._sendCommand('DPAD_LEFT')
    
    def cursorRight(self):
        return self._sendCommand('DPAD_RIGHT')
    
    def cursorEnter(self):
        return self._sendCommand('DPAD_CENTER')
    
    def home(self):
        return self._sendCommand('HOME')
    
    def back(self):
        return self._sendCommand('BACK')
    
    def channelUp(self):
        return self._sendCommand('CHANNEL_UP')
    
    def channelDown(self):
        return self._sendCommand('CHANNEL_DOWN')
    
    def launchApp(self, app):
        try:
            self._atv.send_launch_app_command(apps.Apps[app]['url'])
            return True
        except ConnectionClosed:
            LOG.error('Cannot send command, connection lost: %s', self.identifier)
            return False