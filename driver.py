import asyncio
import logging
import json
import os

from zeroconf import ServiceStateChange, Zeroconf
from zeroconf.asyncio import AsyncServiceBrowser, AsyncServiceInfo, AsyncZeroconf

import ucapi.api as uc
import ucapi.entities as entities

import tv

LOG = logging.getLogger(__name__)
LOOP = asyncio.get_event_loop()
LOG.setLevel(logging.DEBUG)

# Global variables
dataPath = None
api = uc.IntegrationAPI(LOOP)
discoveredAndroidTvs = []
pairingAndroidTv = None
configuredAndroidTvs = {}
    
async def clearConfig() -> None:
    global config
    config = []

    if os.path.exists(dataPath + '/config.json'):
        os.remove(dataPath + '/config.json')

    if os.path.exists(dataPath + '/androidtv_remote_cert.pem'):
        os.remove(dataPath + '/androidtv_remote_cert.pem')

    if os.path.exists(dataPath + '/androidtv_remote_key.pem'):
        os.remove(dataPath + '/androidtv_remote_key.pem')

async def storeCofig() -> None:
    global config
    f = None
    try:
        f= open(dataPath + '/config.json', 'w+')
    except OSError:
        LOG.error('Cannot write the config file')
        return

    json.dump(config, f, ensure_ascii=False)

    f.close()

async def loadConfig() -> bool:
    global config
    f = None
    try:
        f = open(dataPath + '/config.json', 'r')
    except OSError:
        LOG.error('Cannot open the config file')
    
    if f is None:
        return False

    try:
        data = json.load(f)
        f.close()
    except ValueError:
        LOG.error('Empty config file')
        return False

    config = data

    if not config:
        return False

    return True


async def discoverAndroidTvs(timeout: int = 5) -> None:
    def _onServiceStateChanged(zeroconf: Zeroconf, service_type: str, name: str, state_change: ServiceStateChange) -> None:
        if state_change is not ServiceStateChange.Added:
            return
        
        LOG.debug('Found service: %s, %s', service_type, name)
        _ = asyncio.ensure_future(displayServiceInfo(zeroconf, service_type, name))
    
    async def displayServiceInfo(zeroconf: Zeroconf, service_type: str, name: str) -> None:
        global discoveredAndroidTvs
        info = AsyncServiceInfo(service_type, name)
        await info.async_request(zeroconf, 3000)

        nameFinal = name
        nameStr = name.split('.')
        if nameStr:
            nameFinal = nameStr[0]

        tv = { 'name': nameFinal }

        if info:
            addresses = info.parsed_scoped_addresses()
            if addresses:
                tv['address'] = addresses[0]
                discoveredAndroidTvs.append(tv)
        else:
            LOG.debug('No info for %s', name)

    aiozc = AsyncZeroconf()
    services = ["_androidtvremote2._tcp.local."]
    LOG.debug('Looking for Android TV services')

    aiobrowser = AsyncServiceBrowser(
        aiozc.zeroconf, services, handlers=[_onServiceStateChanged]
    )

    await asyncio.sleep(timeout)
    await aiobrowser.async_cancel()
    await aiozc.async_close()
    LOG.debug('Discovery finished')


# DRIVER SETUP
@api.events.on(uc.uc.EVENTS.SETUP_DRIVER)
async def event_handler(websocket, id, data):
    global discoveredAndroidTvs

    LOG.debug('Starting driver setup')
    await clearConfig()
    await api.acknowledgeCommand(websocket, id)
    await api.driverSetupProgress(websocket)

    LOG.debug('Starting Android TV discovery')
    await discoverAndroidTvs()
    dropdownItems = []

    for tv in discoveredAndroidTvs:
        tvData = {
            'id': tv['address'],
            'label': {
                'en': tv['name']
            }
        }

        dropdownItems.append(tvData)

    if not dropdownItems:
        LOG.warning('No Android TVs found')
        await api.driverSetupError(websocket)
        return

    await api.requestDriverSetupUserInput(websocket, 'Please choose your Android TV', [
        { 
        'field': { 
            'dropdown': {
                'value': dropdownItems[0]['id'],
                'items': dropdownItems
            }
        },
        'id': 'choice',
        'label': { 'en': 'Choose your Android TV' }
        }
    ])

@api.events.on(uc.uc.EVENTS.SETUP_DRIVER_USER_DATA)
async def event_handler(websocket, id, data):
    global discoveredAndroidTvs
    global pairingAndroidTv
    global config

    await api.acknowledgeCommand(websocket, id)
    await api.driverSetupProgress(websocket)

    # We pair with companion second
    if "pin" in data:
        LOG.debug('User has entered the PIN')

        res = await pairingAndroidTv.finishPairing(data['pin'])

        if res is False:
            await api.driverSetupError(websocket)
            return
        
        config.append({
            'id': pairingAndroidTv.identifier,
            'name': pairingAndroidTv.name,
            'address': pairingAndroidTv.address
        })
        await storeCofig()

        addAvailableAndroidTv(pairingAndroidTv.identifier, pairingAndroidTv.name)

        pairingAndroidTv.disconnect()
        pairingAndroidTv = None

        await asyncio.sleep(1)
        await api.driverSetupComplete(websocket)

        
    elif "choice" in data:
        choice = data['choice']
        LOG.debug('Chosen Android TV: ' + choice)

        name = ""

        for discoveredTv in discoveredAndroidTvs:
            if discoveredTv['address'] == choice:
                name = discoveredTv['name']

        pairingAndroidTv = tv.AndroidTv(LOOP, dataPath)
        res = await pairingAndroidTv.init(choice, name)
        if res is False:
            await api.driverSetupError(websocket)
            return
        
        LOG.debug('Pairing process begin')

        await api.requestDriverSetupUserInput(websocket, 'Please enter the PIN from your Android TV', [
            { 
            'field': { 
                'text': { 'value': '000000' }
            },
            'id': 'pin',
            'label': { 'en': 'Android TV PIN' }
            }
        ])

        await pairingAndroidTv.startPairing()

    else:
        LOG.error('No choice was received')
        await api.driverSetupError(websocket)

@api.events.on(uc.uc.EVENTS.CONNECT)
async def event_handler():
    await api.setDeviceState(uc.uc.DEVICE_STATES.CONNECTED)
        

@api.events.on(uc.uc.EVENTS.DISCONNECT)
async def event_handler():
    await api.setDeviceState(uc.uc.DEVICE_STATES.DISCONNECTED)


@api.events.on(uc.uc.EVENTS.ENTER_STANDBY)
async def event_handler():
    global configuredAndroidTvs

    for androidTv in configuredAndroidTvs:
        configuredAndroidTvs[androidTv].disconnect()


@api.events.on(uc.uc.EVENTS.EXIT_STANDBY)
async def event_handler():
    global configuredAndroidTvs

    await asyncio.sleep(2)

    for androidTv in configuredAndroidTvs:
        await configuredAndroidTvs[androidTv].connect()


@api.events.on(uc.uc.EVENTS.SUBSCRIBE_ENTITIES)
async def event_handler(entityIds):
    global configuredAndroidTvs

    for entityId in entityIds:
        api.configuredEntities.updateEntityAttributes(entityId, {
            entities.media_player.ATTRIBUTES.STATE: entities.media_player.STATES.UNAVAILABLE
        })

        host = None

        for item in config:
            if item['id'] == entityId:
                host = item['address']

        if host is not None:
            androidTv = tv.AndroidTv(LOOP, dataPath)
            res = await androidTv.init(host, item['name'])

            if res is False:
                await api.setDeviceState(uc.uc.DEVICE_STATES.ERROR)
                return

            @androidTv.events.on(tv.EVENTS.CONNECTED)
            async def _onConnected(identifier):
                await handleConnected(identifier)

            @androidTv.events.on(tv.EVENTS.DISCONNECTED)
            async def _onDisconnected(identifier):
                await handleDisconnected(identifier)

            @androidTv.events.on(tv.EVENTS.ERROR)
            async def _onError(identifier):
                await handleError(identifier)

            @androidTv.events.on(tv.EVENTS.UPDATE)
            async def onUpdate(update):
                await handleAndroidTvUpdate(entityId, update)

            await androidTv.connect()
            configuredAndroidTvs[entityId] = androidTv
        else:
            LOG.error('Failed to create Android TV object')


@api.events.on(uc.uc.EVENTS.UNSUBSCRIBE_ENTITIES)
async def event_handler(entityIds):
    global configuredAndroidTvs

    for entityId in entityIds:
        configuredAndroidTvs[entityId].disconnect()
        configuredAndroidTvs[entityId].events.remove_all_listeners()

@api.events.on(uc.uc.EVENTS.ENTITY_COMMAND)
async def event_handler(websocket, id, entityId, entityType, cmdId, params):
    global configuredAndroidTvs

    androidTv = configuredAndroidTvs[entityId]

    if cmdId == entities.media_player.COMMANDS.PLAY_PAUSE:
        res = androidTv.playPause()
        await api.acknowledgeCommand(websocket, id, uc.uc.STATUS_CODES.OK if res is True else uc.uc.STATUS_CODES.SERVER_ERROR)
    elif cmdId == entities.media_player.COMMANDS.NEXT:
        res = androidTv.next()
        await api.acknowledgeCommand(websocket, id, uc.uc.STATUS_CODES.OK if res is True else uc.uc.STATUS_CODES.SERVER_ERROR)
    elif cmdId == entities.media_player.COMMANDS.PREVIOUS:
        res = androidTv.previous()
        await api.acknowledgeCommand(websocket, id, uc.uc.STATUS_CODES.OK if res is True else uc.uc.STATUS_CODES.SERVER_ERROR)
    elif cmdId == entities.media_player.COMMANDS.VOLUME_UP:
        res = androidTv.volumeUp()
        await api.acknowledgeCommand(websocket, id, uc.uc.STATUS_CODES.OK if res is True else uc.uc.STATUS_CODES.SERVER_ERROR)
    elif cmdId == entities.media_player.COMMANDS.VOLUME_DOWN:
        res = androidTv.volumeDown()
        await api.acknowledgeCommand(websocket, id, uc.uc.STATUS_CODES.OK if res is True else uc.uc.STATUS_CODES.SERVER_ERROR)
    elif cmdId == entities.media_player.COMMANDS.MUTE_TOGGLE:
        res = androidTv.muteToggle()
        await api.acknowledgeCommand(websocket, id, uc.uc.STATUS_CODES.OK if res is True else uc.uc.STATUS_CODES.SERVER_ERROR)
    elif cmdId == entities.media_player.COMMANDS.ON:
        res = androidTv.turnOn()
        await api.acknowledgeCommand(websocket, id, uc.uc.STATUS_CODES.OK if res is True else uc.uc.STATUS_CODES.SERVER_ERROR)
    elif cmdId == entities.media_player.COMMANDS.OFF:
        res =androidTv.turnOff()
        await api.acknowledgeCommand(websocket, id, uc.uc.STATUS_CODES.OK if res is True else uc.uc.STATUS_CODES.SERVER_ERROR)
    elif cmdId == entities.media_player.COMMANDS.CURSOR_UP:
        res =androidTv.cursorUp()
        await api.acknowledgeCommand(websocket, id, uc.uc.STATUS_CODES.OK if res is True else uc.uc.STATUS_CODES.SERVER_ERROR)
    elif cmdId == entities.media_player.COMMANDS.CURSOR_DOWN:
        res = androidTv.cursorDown()
        await api.acknowledgeCommand(websocket, id, uc.uc.STATUS_CODES.OK if res is True else uc.uc.STATUS_CODES.SERVER_ERROR)
    elif cmdId == entities.media_player.COMMANDS.CURSOR_LEFT:
        res = androidTv.cursorLeft()
        await api.acknowledgeCommand(websocket, id, uc.uc.STATUS_CODES.OK if res is True else uc.uc.STATUS_CODES.SERVER_ERROR)
    elif cmdId == entities.media_player.COMMANDS.CURSOR_RIGHT:
        res = androidTv.cursorRight()
        await api.acknowledgeCommand(websocket, id, uc.uc.STATUS_CODES.OK if res is True else uc.uc.STATUS_CODES.SERVER_ERROR)
    elif cmdId == entities.media_player.COMMANDS.CURSOR_ENTER:
        res = androidTv.cursorEnter()
        await api.acknowledgeCommand(websocket, id, uc.uc.STATUS_CODES.OK if res is True else uc.uc.STATUS_CODES.SERVER_ERROR)
    elif cmdId == entities.media_player.COMMANDS.HOME:
        res = androidTv.home()
        await api.acknowledgeCommand(websocket, id, uc.uc.STATUS_CODES.OK if res is True else uc.uc.STATUS_CODES.SERVER_ERROR)
    elif cmdId == entities.media_player.COMMANDS.BACK:
        res = androidTv.back()
        await api.acknowledgeCommand(websocket, id, uc.uc.STATUS_CODES.OK if res is True else uc.uc.STATUS_CODES.SERVER_ERROR)
    elif cmdId == entities.media_player.COMMANDS.CHANNEL_DOWN:
        res = androidTv.channelDown()
        await api.acknowledgeCommand(websocket, id, uc.uc.STATUS_CODES.OK if res is True else uc.uc.STATUS_CODES.SERVER_ERROR)
    elif cmdId == entities.media_player.COMMANDS.CHANNEL_UP:
        res = androidTv.channelUp()
        await api.acknowledgeCommand(websocket, id, uc.uc.STATUS_CODES.OK if res is True else uc.uc.STATUS_CODES.SERVER_ERROR)
    elif cmdId == entities.media_player.COMMANDS.SELECT_SOURCE:
        res = androidTv.launchApp(params["source"])
        await api.acknowledgeCommand(websocket, id, uc.uc.STATUS_CODES.OK if res is True else uc.uc.STATUS_CODES.SERVER_ERROR)


async def handleConnected(identifier):
    LOG.debug('Android TV connected: %s', identifier)
    api.configuredEntities.updateEntityAttributes(identifier, {
        entities.media_player.ATTRIBUTES.STATE: entities.media_player.STATES.STANDBY
    })

async def handleDisconnected(identifier):
    LOG.debug('Android TV disconnected: %s', identifier)
    api.configuredEntities.updateEntityAttributes(identifier, {
        entities.media_player.ATTRIBUTES.STATE: entities.media_player.STATES.UNAVAILABLE
    })

async def handleError(identifier):
    LOG.debug('Android TV error: %s', identifier)
    api.configuredEntities.updateEntityAttributes(identifier, {
        entities.media_player.ATTRIBUTES.STATE: entities.media_player.STATES.UNAVAILABLE
    })
    api.setDeviceState(uc.uc.DEVICE_STATES.ERROR)

async def handleAndroidTvUpdate(entityId, update):
    attributes = {}

    configuredEntity = api.configuredEntities.getEntity(entityId)

    if 'state' in update:
        if update['state'] == 'ON':
            attributes[entities.media_player.ATTRIBUTES.STATE] = entities.media_player.STATES.ON
        elif update['state'] == 'PLAYING':
            attributes[entities.media_player.ATTRIBUTES.STATE] = entities.media_player.STATES.PLAYING
        else:
            attributes[entities.media_player.ATTRIBUTES.STATE] = entities.media_player.STATES.OFF

    if 'title' in update:
        attributes[entities.media_player.ATTRIBUTES.MEDIA_TITLE] = update['title']

    if 'volume' in update:
        attributes[entities.media_player.ATTRIBUTES.VOLUME] = update['volume']

    if 'muted' in update:
        attributes[entities.media_player.ATTRIBUTES.MUTED] = update['muted']

    if 'source_list' in update:
        attributes[entities.media_player.ATTRIBUTES.SOURCE_LIST] = update['source_list']

    if 'source' in update:
        attributes[entities.media_player.ATTRIBUTES.SOURCE] = update['source']
    
    if attributes:
        api.configuredEntities.updateEntityAttributes(entityId, attributes)

def addAvailableAndroidTv(identifier: str, name: str) -> None:
    entity = entities.media_player.MediaPlayer(identifier, name, [
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
        entities.media_player.FEATURES.MEDIA_TITLE
    ], {
        entities.media_player.ATTRIBUTES.STATE: entities.media_player.STATES.UNAVAILABLE,
        entities.media_player.ATTRIBUTES.VOLUME: 0,
        entities.media_player.ATTRIBUTES.MUTED: False,
        entities.media_player.ATTRIBUTES.MEDIA_TITLE: "",
    }, deviceClass = entities.media_player.DEVICECLASSES.TV)

    api.availableEntities.addEntity(entity)


async def main():
    global dataPath

    dataPath = api.configDirPath

    res = await loadConfig()
    if res is True:
        for item in config:
            addAvailableAndroidTv(item['id'], item['name'])

    await api.init('driver.json')

if __name__ == "__main__":
    LOOP.run_until_complete(main())
    LOOP.run_forever()