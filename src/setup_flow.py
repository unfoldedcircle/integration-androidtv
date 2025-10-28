"""
Setup flow for Android TV Remote integration.

:copyright: (c) 2023-2024 by Unfolded Circle ApS.
:license: MPL-2.0, see LICENSE for more details.
"""

import asyncio
import json
import logging
import os
from enum import IntEnum
from pathlib import Path

import ucapi
from ucapi import (
    AbortDriverSetup,
    DriverSetupRequest,
    IntegrationSetupError,
    RequestUserInput,
    SetupAction,
    SetupComplete,
    SetupDriver,
    SetupError,
    UserDataResponse,
)

import config
import discover
import tv
from adb_tv import adb_connect, get_installed_apps, is_authorised
from apps import Apps, IdMappings
from config import AtvDevice, _get_config_root
from external_metadata import get_app_metadata

_LOG = logging.getLogger(__name__)


class SetupSteps(IntEnum):
    """Enumeration of setup steps to keep track of user data responses."""

    INIT = 0
    CONFIGURATION_MODE = 1
    DISCOVER = 2
    DEVICE_CHOICE = 3
    PAIRING_PIN = 4
    APP_SELECTION = 5
    RECONFIGURE = 6


_setup_step = SetupSteps.INIT
_cfg_add_device: bool = False
_discovered_android_tvs: list[dict[str, str]] = []
_pairing_android_tv: tv.AndroidTv | None = None
_use_external_metadata: bool = False
_reconfigured_device: AtvDevice | None = None
_use_chromecast: bool = False
_use_adb: bool = False
_adb_device_id: str = ""
_device_info: dict[str, str] = {}
_use_chromecast_volume: bool = False
_volume_step: int = 10

# TODO #9 externalize language texts
_user_input_discovery = RequestUserInput(
    {"en": "Setup mode", "de": "Setup Modus", "fr": "Installation"},
    [
        {
            "id": "info",
            "label": {
                "en": "Discover or connect to Android TV device",
                "de": "Suche oder Verbinde auf Android TV Gerät",
                "fr": "Découverte ou connexion à l'appareil Android TV",
            },
            "field": {
                "label": {
                    "value": {
                        "en": "Leave blank to use auto-discovery.",
                        "de": "Leer lassen, um automatische Erkennung zu verwenden.",
                        "fr": "Laissez le champ vide pour utiliser la découverte automatique.",
                    }
                }
            },
        },
        {
            "field": {"text": {"value": ""}},
            "id": "address",
            "label": {"en": "IP address", "de": "IP-Adresse", "fr": "Adresse IP"},
        },
    ],
)


# pylint: disable=too-many-return-statements
async def driver_setup_handler(msg: SetupDriver) -> SetupAction:
    """
    Dispatch driver setup requests to corresponding handlers.

    Either start the setup process or handle the selected Android TV device.

    :param msg: the setup driver request object, either DriverSetupRequest or UserDataResponse
    :return: the setup action on how to continue
    """
    global _setup_step
    global _cfg_add_device
    global _pairing_android_tv

    if isinstance(msg, DriverSetupRequest):
        _setup_step = SetupSteps.INIT
        _cfg_add_device = False
        return await handle_driver_setup(msg)

    if isinstance(msg, UserDataResponse):
        _LOG.debug("UserDataResponse: %s %s", msg, _setup_step)
        if _setup_step == SetupSteps.CONFIGURATION_MODE and "action" in msg.input_values:
            return await handle_configuration_mode(msg)
        if _setup_step == SetupSteps.DISCOVER and "address" in msg.input_values:
            return await _handle_discovery(msg)
        if _setup_step == SetupSteps.DEVICE_CHOICE and "choice" in msg.input_values:
            return await handle_device_choice(msg)
        if _setup_step == SetupSteps.PAIRING_PIN and "pin" in msg.input_values:
            return await handle_user_data_pin(msg)
        if _setup_step == SetupSteps.APP_SELECTION:
            return await handle_app_selection(msg)
        if _setup_step == SetupSteps.RECONFIGURE:
            return await _handle_device_reconfigure(msg)
        _LOG.error("No or invalid user response was received: %s", msg)
    elif isinstance(msg, AbortDriverSetup):
        _LOG.info("Setup was aborted with code: %s", msg.error)
        if _pairing_android_tv is not None:
            _pairing_android_tv.disconnect()
            _pairing_android_tv = None
        _setup_step = SetupSteps.INIT

    # user confirmation not used in setup process
    # if isinstance(msg, UserConfirmationResponse):
    #     return handle_user_confirmation(msg)

    return SetupError()


async def handle_driver_setup(msg: DriverSetupRequest) -> RequestUserInput | SetupError:
    """
    Start driver setup.

    Initiated by Remote Two to set up the driver. The reconfigure flag determines the setup flow:

    - Reconfigure is True: show the configured devices and ask user what action to perform (add, delete, reset).
    - Reconfigure is False: clear the existing configuration and show device discovery screen.
    Ask user to enter ip-address for manual configuration, otherwise auto-discovery is used.

    :param msg: driver setup request data, only `reconfigure` flag is of interest.
    :return: the setup action on how to continue
    """
    global _setup_step

    reconfigure = msg.reconfigure
    _LOG.debug("Starting driver setup, reconfigure=%s", reconfigure)

    # workaround for web-configurator not picking up first response
    await asyncio.sleep(1)

    if reconfigure:
        # make sure configuration is up-to-date
        if config.devices.migration_required():
            await config.devices.migrate()
        _setup_step = SetupSteps.CONFIGURATION_MODE

        # get all configured devices for the user to choose from
        dropdown_devices = []
        for device in config.devices.all():
            prefix = "! " if device.auth_error else ""
            model = f"{device.manufacturer} {device.model}"[:30]
            dropdown_devices.append(
                {
                    "id": device.id,
                    "label": {"en": f"{prefix}{device.name} ({device.id}) {model}"},
                }
            )

        # TODO #9 externalize language texts
        # build user actions, based on available devices
        selected_action_index = 0
        dropdown_actions = [
            {
                "id": "add",
                "label": {
                    "en": "Add a new device",
                    "de": "Neues Gerät hinzufügen",
                    "fr": "Ajouter un nouvel appareil",
                },
            },
        ]

        # add remove & reset actions if there's at least one configured device
        if dropdown_devices:
            # pre-select configure action if at least one device exists
            selected_action_index = 1
            dropdown_actions.append(
                {
                    "id": "configure",
                    "label": {
                        "en": "Configure selected device",
                        "de": "Selektiertes Gerät konfigurieren",
                        "fr": "Configurer l'appareil sélectionné",
                    },
                },
            )

            dropdown_actions.append(
                {
                    "id": "remove",
                    "label": {
                        "en": "Delete selected device",
                        "de": "Selektiertes Gerät löschen",
                        "fr": "Supprimer l'appareil sélectionné",
                    },
                },
            )

            dropdown_actions.append(
                {
                    "id": "reset",
                    "label": {
                        "en": "Reset configuration and reconfigure",
                        "de": "Konfiguration zurücksetzen und neu konfigurieren",
                        "fr": "Réinitialiser la configuration et reconfigurer",
                    },
                },
            )
        else:
            # dummy entry if no devices are available
            dropdown_devices.append({"id": "", "label": {"en": "---"}})

        return RequestUserInput(
            {"en": "Configuration mode", "de": "Konfigurations-Modus"},
            [
                {
                    "field": {
                        "dropdown": {
                            "value": dropdown_devices[0]["id"],
                            "items": dropdown_devices,
                        }
                    },
                    "id": "choice",
                    "label": {
                        "en": "Configured devices",
                        "de": "Konfigurierte Geräte",
                        "fr": "Appareils configurés",
                    },
                },
                {
                    "field": {
                        "dropdown": {
                            "value": dropdown_actions[selected_action_index]["id"],
                            "items": dropdown_actions,
                        }
                    },
                    "id": "action",
                    "label": {
                        "en": "Action",
                        "de": "Aktion",
                        "fr": "Appareils configurés",
                    },
                },
            ],
        )

    # Initial setup, make sure we have a clean configuration
    config.devices.clear()  # triggers device instance removal
    _setup_step = SetupSteps.DISCOVER
    return _user_input_discovery


async def handle_configuration_mode(
    msg: UserDataResponse,
) -> RequestUserInput | SetupComplete | SetupError:
    """
    Process user data response in a setup process.

    If ``address`` field is set by the user: try connecting to device and retrieve model information.
    Otherwise, start Android TV discovery and present the found devices to the user to choose from.

    :param msg: response data from the requested user data
    :return: the setup action on how to continue
    """
    global _setup_step
    global _cfg_add_device
    global _reconfigured_device

    action = msg.input_values["action"]

    # workaround for web-configurator not picking up first response
    await asyncio.sleep(1)

    match action:
        case "add":
            _cfg_add_device = True
        case "remove":
            choice = msg.input_values["choice"]
            if not config.devices.remove(choice):
                _LOG.warning("Could not remove device from configuration: %s", choice)
                return SetupError(error_type=IntegrationSetupError.OTHER)
            config.devices.store()
            adb_cert_path = Path(os.environ.get("UC_CONFIG_HOME", "./config")) / "certs" / f"adb_{choice}"
            adb_cert_path_pub = Path(os.environ.get("UC_CONFIG_HOME", "./config")) / "certs" / f"adb_{choice}.pub"
            if adb_cert_path.exists():
                adb_cert_path.unlink()
            if adb_cert_path_pub.exists():
                adb_cert_path_pub.unlink()
            appslist_path = Path(os.environ.get("UC_CONFIG_HOME", "./config")) / f"appslist_{choice}.json"
            if appslist_path.exists():
                appslist_path.unlink()
            _LOG.info("Device removed from configuration: %s", choice)
            return SetupComplete()
        case "configure":
            # Reconfigure device if the identifier has changed
            choice = msg.input_values["choice"]
            selected_device = config.devices.get(choice)
            if not selected_device:
                _LOG.warning("Can not configure device from configuration: %s", choice)
                return SetupError(error_type=IntegrationSetupError.OTHER)

            _setup_step = SetupSteps.RECONFIGURE
            _reconfigured_device = selected_device
            use_chromecast = selected_device.use_chromecast if selected_device.use_chromecast else False
            use_adb = selected_device.use_adb if selected_device.use_adb else False
            use_chromecast_volume = (
                selected_device.use_chromecast_volume if selected_device.use_chromecast_volume else False
            )
            use_external_metadata = (
                selected_device.use_external_metadata if selected_device.use_external_metadata else False
            )
            volume_step = selected_device.volume_step if selected_device.volume_step else 10

            return RequestUserInput(
                {
                    "en": "Configure your Android TV",
                    "de": "Konfiguriere deinen Android TV",
                    "fr": "Configurez votre Android TV",
                },
                [
                    __cfg_use_chromecast(use_chromecast),
                    __cfg_chromecast_volume(use_chromecast_volume),
                    __cfg_volume_step(volume_step),
                    __cfg_external_metadata(use_external_metadata),
                    __cfg_adb(use_adb),
                ],
            )
        case "reset":
            config.devices.clear()  # triggers device instance removal
        case _:
            _LOG.error("Invalid configuration action: %s", action)
            return SetupError(error_type=IntegrationSetupError.OTHER)

    _setup_step = SetupSteps.DISCOVER
    return _user_input_discovery


async def _handle_discovery(msg: UserDataResponse) -> RequestUserInput | SetupError:
    """
    Process user data response from the first setup process screen.

    If ``address`` field is set by the user: try connecting to device and retrieve device information.
    Otherwise, start Apple TV discovery and present the found devices to the user to choose from.

    :param msg: response data from the requested user data
    :return: the setup action on how to continue
    """
    global _discovered_android_tvs
    global _pairing_android_tv
    global _setup_step

    # clear all configured devices and any previous pairing attempt
    if _pairing_android_tv:
        _pairing_android_tv.disconnect()
        _pairing_android_tv = None

    dropdown_items = []
    address = msg.input_values["address"]

    if address:
        _LOG.debug("Starting manual driver setup for %s", address)
        # Connect to device and retrieve name
        certfile = config.devices.default_certfile()
        keyfile = config.devices.default_keyfile()
        android_tv = tv.AndroidTv(certfile, keyfile, AtvDevice(address=address, name="", id=""))

        res = await android_tv.init(20)
        if res is False:
            return _setup_error_from_device_state(android_tv.state)

        existing = config.devices.get(android_tv.identifier)
        if _cfg_add_device and existing and not existing.auth_error:
            _LOG.info(
                "Manually specified device '%s' %s: already configured",
                existing.name,
                android_tv.identifier,
            )
            # no better error code at the moment
            return SetupError(error_type=IntegrationSetupError.OTHER)
        dropdown_items.append({"id": address, "label": {"en": f"{android_tv.name} [{address}]"}})
    else:
        _LOG.debug("Starting driver setup with Android TV discovery")
        # start discovery
        _discovered_android_tvs = await discover.android_tvs()

        # only add new devices or configured devices requiring new pairing
        for discovered_tv in _discovered_android_tvs:
            tv_data = {
                "id": discovered_tv["address"],
                "label": {"en": discovered_tv["label"]},
            }
            existing = config.devices.get_by_name_or_address(discovered_tv["name"], discovered_tv["address"])
            if _cfg_add_device and existing and not existing.auth_error:
                _LOG.info(
                    "Skipping found device '%s' %s: already configured",
                    discovered_tv["name"],
                    discovered_tv["address"],
                )
                continue
            dropdown_items.append(tv_data)

    if not dropdown_items:
        _LOG.warning("No Android TVs found")
        return SetupError(error_type=IntegrationSetupError.NOT_FOUND)

    _setup_step = SetupSteps.DEVICE_CHOICE
    # TODO #9 externalize language texts
    return RequestUserInput(
        title={
            "en": "Please choose your Android TV",
            "de": "Bitte Android TV auswählen",
            "fr": "Choisir votre Android TV",
        },
        settings=[
            {
                "field": {
                    "dropdown": {
                        "value": dropdown_items[0]["id"],
                        "items": dropdown_items,
                    }
                },
                "id": "choice",
                "label": {
                    "en": "Choose your Android TV",
                    "de": "Wähle deinen Android TV",
                    "fr": "Choisir votre Android TV",
                },
            },
            __cfg_use_chromecast(False),
            __cfg_chromecast_volume(False),
            __cfg_volume_step(10),
            __cfg_external_metadata(False),
            __cfg_adb(False),
        ],
    )


async def handle_device_choice(msg: UserDataResponse) -> RequestUserInput | SetupError:
    """
    Process user data device choice response in a setup process.

    Driver setup callback to provide requested user data during the setup process.

    :param msg: response data from the requested user data
    :return: the setup action on how to continue.
    """
    global _pairing_android_tv
    global _use_chromecast
    global _use_chromecast_volume
    global _setup_step
    global _use_external_metadata
    global _use_adb
    global _volume_step

    choice = msg.input_values["choice"]
    _use_external_metadata = msg.input_values.get("external_metadata", "false") == "true"
    _use_chromecast = msg.input_values.get("chromecast", "false") == "true"
    _use_chromecast_volume = msg.input_values.get("chromecast_volume", "false") == "true"
    _volume_step = int(msg.input_values.get("volume_step", 10))
    _use_adb = msg.input_values.get("adb", "false") == "true"
    name = ""

    for discovered_tv in _discovered_android_tvs:
        if discovered_tv["address"] == choice:
            name = discovered_tv["name"]

    certfile = config.devices.default_certfile()
    keyfile = config.devices.default_keyfile()
    _pairing_android_tv = tv.AndroidTv(
        certfile,
        keyfile,
        AtvDevice(
            address=choice,
            name=name,
            id="",
            use_external_metadata=False,
            use_chromecast=False,
            use_adb=False,
            use_chromecast_volume=_use_chromecast_volume,
            volume_step=_volume_step,
        ),
    )
    _LOG.info("Chosen Android TV: %s. Start pairing process...", choice)

    res = await _pairing_android_tv.init(20)
    if res is False:
        return _setup_error_from_device_state(_pairing_android_tv.state)

    _LOG.info("[%s] Pairing process begin", name)

    res = await _pairing_android_tv.start_pairing()
    if res == ucapi.StatusCodes.OK:
        _setup_step = SetupSteps.PAIRING_PIN
        # TODO #9 externalize language texts
        return RequestUserInput(
            {
                "en": "Please enter the PIN shown on your Android TV",
                "de": "Bitte gib die auf deinem Android-Fernseher angezeigte PIN ein",
                "fr": "Veuillez saisir le code PIN affiché sur votre Android TV",
            },
            [
                {
                    "field": {"text": {"value": "000000"}},
                    "id": "pin",
                    "label": {"en": "Android TV PIN"},
                }
            ],
        )

    return _setup_error_from_device_state(_pairing_android_tv.state)


async def handle_user_data_pin(msg: UserDataResponse) -> RequestUserInput | SetupComplete | SetupError:
    """
    Process user data pairing pin response in a setup process.

    :param msg: response data from the requested user data
    :return: the setup action on how to continue.
    """
    global _pairing_android_tv
    global _setup_step

    _LOG.debug("Entered handle_user_data_pin with msg: %s", msg)

    if _pairing_android_tv is None:
        _LOG.error("Can't handle pairing pin: no device instance! Aborting setup")
        return SetupError()

    _LOG.info("[%s] User has entered the PIN", _pairing_android_tv.log_id)

    res = await _pairing_android_tv.finish_pairing(msg.input_values["pin"])
    _LOG.debug("[%s] finish_pairing result: %s", _pairing_android_tv.log_id, res)

    _pairing_android_tv.disconnect()
    _LOG.debug("[%s] Disconnected after pairing attempt", _pairing_android_tv.log_id)

    if res != ucapi.StatusCodes.OK:
        _LOG.warning("[%s] Pairing failed", _pairing_android_tv.log_id)
        return SetupError()

    _LOG.info("[%s] Pairing done, retrieving device information", _pairing_android_tv.log_id)
    timeout = int(tv.CONNECTION_TIMEOUT)
    res = ucapi.StatusCodes.SERVER_ERROR
    _device_info = None

    if await _pairing_android_tv.init(timeout):
        _LOG.debug("[%s] Initialization successful", _pairing_android_tv.log_id)
        if await _pairing_android_tv.connect(timeout):
            _LOG.debug("[%s] Connection successful", _pairing_android_tv.log_id)
            _device_info = _pairing_android_tv.device_info or {}
            _LOG.debug("[%s] Retrieved device info: %s", _pairing_android_tv.log_id, _device_info)

            if config.devices.assign_default_certs_to_device(_pairing_android_tv.identifier, True):
                res = ucapi.StatusCodes.OK
                _LOG.debug("[%s] Default certificates assigned successfully", _pairing_android_tv.log_id)

    _pairing_android_tv.disconnect()
    _LOG.debug("[%s] Disconnected after retrieving device information", _pairing_android_tv.log_id)

    if res != ucapi.StatusCodes.OK:
        state = _pairing_android_tv.state
        _LOG.info("[%s] Setup failed: %s (state=%s)", _pairing_android_tv.log_id, res, state)
        _pairing_android_tv = None
        return _setup_error_from_device_state(state)

    adb_apps = {}
    if _use_adb:
        _LOG.debug("ADB is enabled, proceeding with ADB setup")

        if not msg.input_values.get("adb", False):
            _LOG.error("ADB setup failed: 'adb' not found in input values")
            return SetupError()

        device_id = _pairing_android_tv.identifier
        ip_address = _pairing_android_tv.address
        _LOG.debug("Attempting ADB setup for device_id: %s, ip_address: %s", device_id, ip_address)

        adb_device = await adb_connect(device_id, ip_address)
        if not adb_device or not await is_authorised(adb_device):
            return SetupError(error_type=IntegrationSetupError.AUTHORIZATION_ERROR)

        _LOG.debug("ADB authorisation confirmed")
        adb_apps = await get_installed_apps(adb_device)
        _LOG.debug("Retrieved ADB apps: %s", adb_apps)
        await adb_device.close()

    offline_friendly_names = set(IdMappings.values())
    # offline_package_ids = set(Apps.keys())

    if _use_adb:
        filtered_adb_apps = {}
        for package, details in adb_apps.items():
            if package in Apps or package in IdMappings:
                continue
            friendly = details.get("name", "")
            if friendly and friendly in offline_friendly_names:
                continue
            filtered_adb_apps[package] = details

        merged_apps = {**filtered_adb_apps, **Apps}
    else:
        merged_apps = Apps

    _LOG.debug("Merged apps (offline preferred): %s", merged_apps)

    app_entries = []

    for package, details in merged_apps.items():
        mapped_name = IdMappings.get(package)
        name = mapped_name or details.get("name", package)
        editable = False

        if _use_external_metadata and not mapped_name:
            try:
                metadata = await get_app_metadata(package)
                if metadata.get("name"):
                    name = metadata["name"]
                    editable = True
            except Exception as e:
                _LOG.warning("Metadata lookup failed for %s: %s", package, e)

        is_unfriendly = name.startswith("com.") or name.count(".") >= 2
        app_entries.append((is_unfriendly, name.lower(), package, name, editable))

    app_entries.sort()

    settings = []

    for _, _, package, name, editable in app_entries:
        is_adb_only = _use_adb and package in adb_apps and package not in Apps

        settings.append(
            {
                "id": f"{package}_enabled",
                "label": {
                    "en": name if not is_adb_only else f"{package}",
                    "de": name if not is_adb_only else f"{package}",
                    "fr": name if not is_adb_only else f"{package}",
                },
                "field": {"checkbox": {"value": False}},
            }
        )

        if is_adb_only or editable:
            settings.append(
                {
                    "id": f"{package}_name",
                    "label": {
                        "en": f"Friendly name for {package}",
                        "de": f"Anzeigename für {package}",
                        "fr": f"Nom convivial pour {package}",
                    },
                    "field": {"text": {"value": name}},
                }
            )

    _setup_step = SetupSteps.APP_SELECTION
    return RequestUserInput(
        title={
            "en": "Select visible apps",
            "de": "Wähle sichtbare Apps",
            "fr": "Sélectionnez les applications visibles",
        },
        settings=settings,
    )


async def handle_app_selection(msg: UserDataResponse) -> SetupComplete | SetupError:
    global _pairing_android_tv

    selected_apps = {}

    for field_id, value in msg.input_values.items():
        if field_id.endswith("_enabled") and str(value).lower() == "true":
            package = field_id.removesuffix("_enabled")
            name_field = f"{package}_name"
            friendly_name = msg.input_values.get(name_field, package)

            # Prefer static app URL if it exists
            static_entry = Apps.get(friendly_name) or Apps.get(package)
            url = static_entry["url"] if static_entry else f"market://launch?id={package}"

            selected_apps[friendly_name] = {"url": url}

    filename = f"appslist_{_pairing_android_tv.identifier}.json"
    apps_file = _get_config_root() / filename
    try:
        apps_file.write_text(json.dumps(selected_apps, indent=2))
        _LOG.info("App selection stored: %s", selected_apps)
    except Exception as e:
        _LOG.error("Failed to write selected apps: %s", e)
        return SetupError()

    device = AtvDevice(
        id=_pairing_android_tv.identifier,
        name=_pairing_android_tv.name,
        address=_pairing_android_tv.address,
        manufacturer=_device_info.get("manufacturer", ""),
        model=_device_info.get("model", ""),
        use_external_metadata=_use_external_metadata,
        use_chromecast=_use_chromecast,
        use_chromecast_volume=_use_chromecast_volume,
        volume_step=_volume_step,
        use_adb=_use_adb,
    )
    _LOG.debug("Created AtvDevice: %s", device)

    config.devices.add_or_update(device)
    _LOG.debug("Device added/updated in configuration")

    config.devices.store()
    _LOG.debug("Configuration stored")

    await asyncio.sleep(1)
    _LOG.info("[%s] Setup successfully completed for %s", device.name, device.id)

    _pairing_android_tv = None

    return SetupComplete()


async def _handle_device_reconfigure(
    msg: UserDataResponse,
) -> SetupComplete | SetupError:
    """
    Process reconfiguration of a registered Android TV device.

    :param msg: response data from the requested user data
    :return: the setup action on how to continue: SetupComplete after updating configuration
    """
    # flake8: noqa:F824
    # pylint: disable=W0602
    global _reconfigured_device

    if _reconfigured_device is None:
        return SetupError()

    use_chromecast = msg.input_values.get("chromecast", "false") == "true"
    use_chromecast_volume = msg.input_values.get("chromecast_volume", "false") == "true"
    use_external_metadata = msg.input_values.get("external_metadata", "false") == "true"
    volume_step = int(msg.input_values.get("volume_step", 10))
    use_adb = msg.input_values.get("adb", "false") == "true"

    _LOG.debug("User has changed configuration")
    _reconfigured_device.use_chromecast = use_chromecast
    _reconfigured_device.use_chromecast_volume = use_chromecast_volume
    _reconfigured_device.use_external_metadata = use_external_metadata
    _reconfigured_device.use_adb = use_adb
    _reconfigured_device.volume_step = volume_step

    config.devices.add_or_update(_reconfigured_device)  # triggers ATV instance update
    await asyncio.sleep(1)
    _LOG.info(
        "Setup successfully completed for %s (chromecast %s, external metadata %s, volume step %s)",
        _reconfigured_device.name,
        _reconfigured_device.use_chromecast,
        _reconfigured_device.use_external_metadata,
        _reconfigured_device.volume_step,
    )

    return SetupComplete()


def _setup_error_from_device_state(state: tv.DeviceState) -> SetupError:
    match state:
        case tv.DeviceState.AUTH_ERROR:
            error_type = IntegrationSetupError.AUTHORIZATION_ERROR
        case tv.DeviceState.TIMEOUT:
            error_type = IntegrationSetupError.TIMEOUT
        case _:
            error_type = IntegrationSetupError.CONNECTION_REFUSED

    return SetupError(error_type=error_type)


def __cfg_use_chromecast(enabled: bool):
    return {
        "id": "chromecast",
        "label": {
            "en": "Preview feature: Enable Chromecast features",
            "de": "Vorschaufunktion: Aktiviere Chromecast-Features",
            "fr": "Fonctionnalité en aperçu: Activer les fonctionnalités de Chromecast",
        },
        "field": {"checkbox": {"value": enabled}},
    }


def __cfg_chromecast_volume(enabled: bool):
    return {
        "id": "chromecast_volume",
        "label": {
            "en": "Preview feature: Set volume through Chromecast",
            "de": "Vorschaufunktion: Lautstärkeregelung mittels Chromecast",
            "fr": "Fonctionnalité en aperçu: Régler le volume par Chromecast",
        },
        "field": {"checkbox": {"value": enabled}},
    }


def __cfg_volume_step(value: int):
    return {
        "id": "volume_step",
        "label": {
            "en": "Volume step in percent (Chromecast only)",
            "de": "Lautstärkeregelung in Prozent (nur Chromecast)",
            "fr": "Pallier de volume en pourcentage (Chromecast uniquement)",
        },
        "field": {"number": {"value": value, "min": 1, "max": 50, "steps": 1, "decimals": 0}},
    }


def __cfg_external_metadata(enabled: bool):
    return {
        "id": "external_metadata",
        "label": {
            "en": "Preview feature: Enable external metadata",
            "de": "Vorschaufunktion: Aktiviere externe Metadaten",
            "fr": "Fonctionnalité en aperçu: Activer les métadonnées externes",
        },
        "field": {"checkbox": {"value": enabled}},
    }


def __cfg_adb(enabled: bool):
    return {
        "id": "adb",
        "label": {
            "en": "Preview feature: Enable ADB connection (for app list)",
            "de": "Vorschaufunktion: Aktiviere ADB Verbindung (für App-Browsing)",
            "fr": "Fonctionnalité en aperçu: Activer la connexion ADB (pour la navigation dans les applications)",
        },
        "field": {"checkbox": {"value": enabled}},
    }
