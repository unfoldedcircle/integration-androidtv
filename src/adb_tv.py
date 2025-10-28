"""
This module provides utilities for interacting with Android TVs via ADB (Android Debug Bridge).

It includes functions for managing ADB keys, connecting to devices, retrieving installed apps,
and verifying device authorization.
"""

import os
from pathlib import Path
from typing import Dict, Optional

from adb_shell.adb_device_async import AdbDeviceTcpAsync
from adb_shell.auth.keygen import keygen
from adb_shell.auth.sign_pythonrsa import PythonRSASigner

ADB_CERTS_DIR = Path(os.environ.get("UC_CONFIG_HOME", "./config")) / "certs"
ADB_CERTS_DIR.mkdir(parents=True, exist_ok=True)


def get_adb_key_paths(device_id: str) -> tuple[Path, Path]:
    """
    Return the paths to the private and public ADB keys for a given device.

    Args:
        device_id (str): The unique identifier for the device.

    Returns:
        tuple[Path, Path]: Paths to the private and public key files.
    """
    priv = ADB_CERTS_DIR / f"adb_{device_id}"
    pub = ADB_CERTS_DIR / f"adb_{device_id}.pub"
    return priv, pub


def load_or_generate_adb_keys(device_id: str) -> PythonRSASigner:
    """
    Ensure ADB RSA keys exist for the device and return the signer.

    Args:
        device_id (str): The unique identifier for the device.

    Returns:
        PythonRSASigner: The signer object for ADB authentication.
    """
    priv_path, pub_path = get_adb_key_paths(device_id)

    if not priv_path.exists() or not pub_path.exists():
        keygen(str(priv_path))

    with open(priv_path, encoding="utf-8") as f:
        priv = f.read()
    with open(pub_path, encoding="utf-8") as f:
        pub = f.read()
    return PythonRSASigner(pub, priv)


async def adb_connect(device_id: str, host: str, port: int = 5555) -> Optional[AdbDeviceTcpAsync]:
    """
    Connect to an Android device via ADB.

    Args:
        device_id (str): The unique identifier for the device.
        host (str): The IP address or hostname of the device.
        port (int, optional): The port number for the ADB connection. Defaults to 5555.

    Returns:
        Optional[AdbDeviceTcpAsync]: The connected ADB device object, or None if the connection fails.
    """
    signer = load_or_generate_adb_keys(device_id)
    device = AdbDeviceTcpAsync(host, port, default_transport_timeout_s=9.0)

    try:
        await device.connect(rsa_keys=[signer], auth_timeout_s=20)
        return device
    except Exception as e:
        print(f"ADB connection failed to {host}:{port} — {e}")
        return None


async def get_installed_apps(device: AdbDeviceTcpAsync) -> Dict[str, Dict[str, str]]:
    """
    Retrieve a list of installed non-system apps in a structured format.

    Args:
        device (AdbDeviceTcpAsync): The connected ADB device.

    Returns:
        Dict[str, Dict[str, str]]: A dictionary of app package names and their metadata.
    """
    output = await device.shell("pm list packages -3 -e")
    packages = sorted(line.replace("package:", "").strip() for line in output.splitlines())
    return {package: {"url": f"market://launch?id={package}"} for package in packages}


async def is_authorised(device: AdbDeviceTcpAsync) -> bool:
    """
    Check if the connected device is authorized for ADB communication.

    Args:
        device (AdbDeviceTcpAsync): The connected ADB device.

    Returns:
        bool: True if the device is authorized, False otherwise.
    """
    try:
        result = await device.shell("echo ADB_OK")
        return "ADB_OK" in result
    except Exception:
        return False
