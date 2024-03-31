"""
Configuration handling of the integration driver.

:copyright: (c) 2023-2024 by Unfolded Circle ApS.
:license: MPL-2.0, see LICENSE for more details.
"""

import dataclasses
import json
import logging
import os
from dataclasses import dataclass
from typing import Iterator

from tv import AndroidTv

_LOG = logging.getLogger(__name__)

_CFG_FILENAME = "config.json"


@dataclass
class AtvDevice:
    """Android TV device configuration."""

    id: str
    """Unique identifier of the device."""
    name: str
    """Friendly name of the device."""
    address: str
    """IP address of device."""
    manufacturer: str
    """Device manufacturer name."""
    model: str
    """Device model name."""
    auth_error: bool = False
    """Authentication error, device requires pairing."""


class _EnhancedJSONEncoder(json.JSONEncoder):
    """Python dataclass json encoder."""

    def default(self, o):
        if dataclasses.is_dataclass(o):
            return dataclasses.asdict(o)
        return super().default(o)


class Devices:
    """Integration driver configuration class. Manages all configured Android TV devices."""

    def __init__(self, data_path: str, add_handler, remove_handler):
        """
        Create a configuration instance for the given configuration path.

        :param data_path: configuration path for the configuration file and client device certificates.
        """
        self._data_path: str = data_path
        self._cfg_file_path: str = os.path.join(data_path, _CFG_FILENAME)
        self._config: list[AtvDevice] = []
        self._add_handler = add_handler
        self._remove_handler = remove_handler
        self.load()

    @property
    def data_path(self) -> str:
        """Return the configuration path."""
        return self._data_path

    def all(self) -> Iterator[AtvDevice]:
        """Get an iterator for all device configurations."""
        return iter(self._config)

    def contains(self, atv_id: str) -> bool:
        """Check if there's a device with the given device identifier."""
        for item in self._config:
            if item.id == atv_id:
                return True
        return False

    def add_or_update(self, atv: AtvDevice) -> None:
        """
        Add a new configured Android TV device and persist configuration.

        The device is updated if it already exists in the configuration.
        """
        # duplicate check
        if not self.update(atv):
            self._config.append(atv)
            self.store()
            if self._add_handler is not None:
                self._add_handler(atv)

    def get(self, atv_id: str) -> AtvDevice | None:
        """
        Get device configuration for given identifier.

        :return: A copy of the device configuration or None if not found.
        """
        for item in self._config:
            if item.id == atv_id:
                # return a copy
                return dataclasses.replace(item)
        return None

    def get_by_name_or_address(self, name: str, address: str) -> AtvDevice | None:
        """
        Get device configuration for a matching name or address.

        :return: A copy of the device configuration or None if not found.
        """
        for item in self._config:
            if item.name == name or item.address == address:
                # return a copy
                return dataclasses.replace(item)
        return None

    def update(self, atv: AtvDevice) -> bool:
        """Update a configured Android TV device and persist configuration."""
        for item in self._config:
            if item.id == atv.id:
                item.address = atv.address
                item.name = atv.name
                item.manufacturer = atv.manufacturer
                item.model = atv.model
                item.auth_error = atv.auth_error
                return self.store()
        return False

    def default_certfile(self) -> str:
        """Return the default certificate file for initializing a device."""
        return os.path.join(self._data_path, "androidtv_remote_cert.pem")

    def default_keyfile(self) -> str:
        """Return the default key file for initializing a device."""
        return os.path.join(self._data_path, "androidtv_remote_key.pem")

    def certfile(self, atv_id: str) -> str:
        """Return the certificate file of the device."""
        return os.path.join(self._data_path, f"androidtv_{atv_id}_remote_cert.pem")

    def keyfile(self, atv_id: str) -> str:
        """Return the key file of the device."""
        return os.path.join(self._data_path, f"androidtv_{atv_id}_remote_key.pem")

    def remove(self, atv_id: str) -> bool:
        """Remove the given device configuration."""
        atv = self.get(atv_id)
        if atv is None:
            return False
        try:
            self.remove_files(atv_id)
            self._config.remove(atv)
            if self._remove_handler is not None:
                self._remove_handler(atv)
            return True
        except ValueError:
            pass
        return False

    def remove_files(self, atv_id: str) -> bool:
        """Remove the certificate and key files of a given Android TV instance."""
        pem_file = self.certfile(atv_id)
        try:
            if os.path.exists(pem_file):
                os.remove(pem_file)
            pem_file = self.keyfile(atv_id)
            if os.path.exists(pem_file):
                os.remove(pem_file)
            return True
        except OSError as ex:
            _LOG.error("Failed to remove certificate file of %s: %s", atv_id, ex)
            return False

    def clear(self) -> None:
        """Remove the configuration file and device certificates."""
        for item in self._config:
            self.remove_files(item.id)

        self._config = []

        if os.path.exists(self._cfg_file_path):
            os.remove(self._cfg_file_path)

        if self._remove_handler is not None:
            self._remove_handler(None)

    def store(self) -> bool:
        """
        Store the configuration file.

        :return: True if the configuration could be saved.
        """
        try:
            with open(self._cfg_file_path, "w+", encoding="utf-8") as f:
                json.dump(self._config, f, ensure_ascii=False, cls=_EnhancedJSONEncoder)
            return True
        except OSError:
            _LOG.error("Cannot write the config file")

        return False

    def load(self) -> bool:
        """
        Load the config into the config global variable.

        :return: True if the configuration could be loaded.
        """
        try:
            with open(self._cfg_file_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            for item in data:
                # not using AtvDevice(**item) to be able to migrate old configuration files with missing attributes
                atv = AtvDevice(
                    item.get("id"),
                    item.get("name"),
                    item.get("address"),
                    item.get("manufacturer", ""),
                    item.get("model", ""),
                    item.get("auth_error", False),
                )
                self._config.append(atv)
            return True
        except OSError as err:
            _LOG.error("Cannot open the config file: %s", err)
        except (AttributeError, ValueError, TypeError) as err:
            _LOG.error("Empty or invalid config file: %s", err)

        return False

    def migration_required(self) -> bool:
        """Check if configuration migration is required."""
        for item in self._config:
            if not item.manufacturer:
                return True

        # Are there old certificate files to rename?
        if os.path.exists(os.path.join(self._data_path, "androidtv_remote_cert.pem")) or os.path.exists(
            os.path.join(self._data_path, "androidtv_remote_key.pem")
        ):
            return True

        return False

    async def migrate(self) -> bool:
        """Migrate configuration if required."""
        result = True
        for item in self._config:
            self.assign_default_certs_to_device(item.id)
            if not item.manufacturer:
                _LOG.info(
                    "Migrating configuration: connecting to device '%s' (%s) to update manufacturer and device model",
                    item.name,
                    item.id,
                )
                android_tv = AndroidTv(self.certfile(item.id), self.keyfile(item.id), item.address, item.name, item.id)
                if await android_tv.init(10) and await android_tv.connect(10):
                    if device_info := android_tv.device_info:
                        item.manufacturer = android_tv.device_info
                        item.manufacturer = device_info.get("manufacturer", "")
                        item.model = device_info.get("model", "")

                        _LOG.info(
                            "Updating device configuration '%s' (%s) with: manufacturer=%s, model=%s",
                            item.name,
                            item.id,
                            item.manufacturer,
                            item.model,
                        )
                        if not self.store():
                            result = False
                    else:
                        result = False
                        _LOG.warning(
                            "Could not migrate device configuration '%s' (%s): device information not available",
                            item.name,
                            item.id,
                        )
                else:
                    result = False
                    _LOG.warning(
                        "Could not migrate device configuration '%s' (%s): device not found on network",
                        item.name,
                        item.id,
                    )
                android_tv.disconnect()

        _LOG.debug("Device configuration migration state: %s", result)
        return result

    def assign_default_certs_to_device(self, atv_id: str) -> bool:
        """
        Assign the default certificate files to the given device.

        :param atv_id: Android TV identifier
        :return: True if the certificates could be assigned, or were already assigned, False if assignment failed
        """
        # Migration of certificate/key files with identifier in name
        old_certfile = self.default_certfile()
        old_keyfile = self.default_keyfile()
        new_certfile = self.certfile(atv_id)
        new_keyfile = self.keyfile(atv_id)
        if (
            os.path.exists(old_certfile)
            and os.path.exists(old_keyfile)
            and not (os.path.exists(new_certfile) and os.path.exists(new_keyfile))
        ):
            try:
                new_file = new_certfile
                _LOG.info(
                    "Rename certificate file %s to %s",
                    os.path.basename(old_certfile),
                    os.path.basename(new_certfile),
                )
                os.rename(old_certfile, new_file)

                new_file = new_keyfile
                _LOG.info(
                    "Rename key file %s to %s",
                    os.path.basename(old_keyfile),
                    os.path.basename(new_file),
                )
                os.rename(old_keyfile, new_file)
            except OSError as ex:
                _LOG.error("Error while migrating certificate file %s: %s", os.path.basename(new_file), ex)
                return False
        return True


devices: Devices | None = None
