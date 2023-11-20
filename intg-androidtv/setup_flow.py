"""
Setup flow for Android TV Remote integration.

:copyright: (c) 2023 by Unfolded Circle ApS.
:license: MPL-2.0, see LICENSE for more details.
"""

import asyncio
import logging
from enum import IntEnum

import config
import discover
import tv
import ucapi
from config import AtvDevice
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

_LOG = logging.getLogger(__name__)


class SetupSteps(IntEnum):
    """Enumeration of setup steps to keep track of user data responses."""

    INIT = 0
    CONFIGURATION_MODE = 1
    DEVICE_CHOICE = 2
    PAIRING_PIN = 3


_setup_step = SetupSteps.INIT
_discovered_android_tvs: list[dict[str, str]] = []
_pairing_android_tv: tv.AndroidTv | None = None


async def driver_setup_handler(msg: SetupDriver) -> SetupAction:
    """
    Dispatch driver setup requests to corresponding handlers.

    Either start the setup process or handle the selected Android TV device.

    :param msg: the setup driver request object, either DriverSetupRequest or UserDataResponse
    :return: the setup action on how to continue
    """
    global _setup_step
    global _pairing_android_tv

    if isinstance(msg, DriverSetupRequest):
        _setup_step = SetupSteps.INIT
        return await handle_driver_setup(msg)
    if isinstance(msg, UserDataResponse):
        _LOG.debug("UserDataResponse: %s", msg)
        if _setup_step == SetupSteps.CONFIGURATION_MODE and "address" in msg.input_values:
            return await handle_configuration_mode(msg)
        if _setup_step == SetupSteps.DEVICE_CHOICE and "choice" in msg.input_values:
            return await handle_device_choice(msg)
        if _setup_step == SetupSteps.PAIRING_PIN and "pin" in msg.input_values:
            return await handle_user_data_pin(msg)
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


async def handle_driver_setup(_msg: DriverSetupRequest) -> RequestUserInput | SetupError:
    """
    Start driver setup.

    Initiated by Remote Two to set up the driver.
    Ask user to enter ip-address for manual configuration, otherwise auto-discovery is used.

    :param _msg: not used, we don't have any input fields in the first setup screen.
    :return: the setup action on how to continue
    """
    global _setup_step

    _LOG.debug("Starting driver setup")
    _setup_step = SetupSteps.CONFIGURATION_MODE
    return RequestUserInput(
        {"en": "Setup mode", "de": "Setup Modus"},
        [
            {"field": {"text": {"value": ""}}, "id": "address", "label": {"en": "IP address", "de": "IP-Adresse"}},
            {
                "id": "info",
                "label": {"en": ""},
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
        ],
    )


async def handle_configuration_mode(msg: UserDataResponse) -> RequestUserInput | SetupError:
    """
    Process user data response in a setup process.

    If ``address`` field is set by the user: try connecting to device and retrieve model information.
    Otherwise, start Android TV discovery and present the found devices to the user to choose from.

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
    config.devices.clear()  # triggers device instance removal

    dropdown_items = []
    address = msg.input_values["address"]

    if address:
        _LOG.debug("Starting manual driver setup for %s", address)
        # Connect to device and retrieve name
        android_tv = tv.AndroidTv(config.devices.data_path, address, "")
        res = await android_tv.init(20)
        if res is False:
            return SetupError(error_type=IntegrationSetupError.TIMEOUT)
        dropdown_items.append({"id": address, "label": {"en": f"{android_tv.name} [{address}]"}})
    else:
        _LOG.debug("Starting driver setup with Android TV discovery")
        # start discovery
        _discovered_android_tvs = await discover.android_tvs()

        for discovered_tv in _discovered_android_tvs:
            tv_data = {"id": discovered_tv["address"], "label": {"en": discovered_tv["label"]}}

            dropdown_items.append(tv_data)

    if not dropdown_items:
        _LOG.warning("No Android TVs found")
        return SetupError(error_type=IntegrationSetupError.NOT_FOUND)

    _setup_step = SetupSteps.DEVICE_CHOICE
    # TODO #9 externalize language texts
    return RequestUserInput(
        {"en": "Please choose your Android TV", "de": "Bitte Android TV auswählen"},
        [
            {
                "field": {"dropdown": {"value": dropdown_items[0]["id"], "items": dropdown_items}},
                "id": "choice",
                "label": {
                    "en": "Choose your Android TV",
                    "de": "Wähle deinen Android TV",
                    "fr": "Choisir votre Android TV",
                },
            }
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
    global _setup_step

    choice = msg.input_values["choice"]
    name = ""

    for discovered_tv in _discovered_android_tvs:
        if discovered_tv["address"] == choice:
            name = discovered_tv["name"]

    _pairing_android_tv = tv.AndroidTv(config.devices.data_path, choice, name)
    _LOG.info("Chosen Android TV: %s. Start pairing process...", choice)

    res = await _pairing_android_tv.init(20)
    if res is False:
        return SetupError(error_type=IntegrationSetupError.TIMEOUT)

    if _pairing_android_tv is None:
        # Setup process was cancelled
        return SetupError()

    _LOG.info("Pairing process begin")

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
            [{"field": {"text": {"value": "000000"}}, "id": "pin", "label": {"en": "Android TV PIN"}}],
        )

    # no better error code right now
    return SetupError(error_type=IntegrationSetupError.CONNECTION_REFUSED)


async def handle_user_data_pin(msg: UserDataResponse) -> SetupComplete | SetupError:
    """
    Process user data pairing pin response in a setup process.

    Driver setup callback to provide requested user data during the setup process.

    :param msg: response data from the requested user data
    :return: the setup action on how to continue: SetupComplete if a valid Android TV device was chosen.
    """
    global _pairing_android_tv

    _LOG.info("User has entered the PIN")

    if _pairing_android_tv is None:
        _LOG.error("Can't handle pairing pin: no device instance! Aborting setup")
        return SetupError()

    res = await _pairing_android_tv.finish_pairing(msg.input_values["pin"])
    _pairing_android_tv.disconnect()

    if res != ucapi.StatusCodes.OK:
        _pairing_android_tv = None
        if res == ucapi.StatusCodes.UNAUTHORIZED:
            return SetupError(error_type=IntegrationSetupError.AUTHORIZATION_ERROR)
        return SetupError(error_type=IntegrationSetupError.CONNECTION_REFUSED)

    device = AtvDevice(_pairing_android_tv.identifier, _pairing_android_tv.name, _pairing_android_tv.address)
    config.devices.add(device)  # triggers AndroidTv instance creation
    config.devices.store()

    # ATV device connection will be triggered with subscribe_entities request

    _pairing_android_tv = None
    await asyncio.sleep(1)

    _LOG.info("Setup successfully completed for %s", device.name)
    return SetupComplete()
