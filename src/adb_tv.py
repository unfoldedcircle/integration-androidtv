import asyncio
import os
import re
from pathlib import Path
from typing import Dict, List, Optional

from adb_shell.adb_device_async import AdbDeviceTcpAsync
from adb_shell.auth.keygen import keygen
from adb_shell.auth.sign_pythonrsa import PythonRSASigner

ADB_CERTS_DIR = Path(os.environ.get("UC_CONFIG_HOME", "./config")) / "certs"
ADB_CERTS_DIR.mkdir(parents=True, exist_ok=True)


def get_adb_key_paths(device_id: str) -> tuple[Path, Path]:
    """Return the path to the private and public adb keys for a given device."""
    priv = ADB_CERTS_DIR / f"adb_{device_id}"
    pub = ADB_CERTS_DIR / f"adb_{device_id}.pub"
    return priv, pub


def load_or_generate_adb_keys(device_id: str) -> PythonRSASigner:
    """Ensure ADB RSA keys exist for the device and return the signer."""
    priv_path, pub_path = get_adb_key_paths(device_id)

    if not priv_path.exists() or not pub_path.exists():
        keygen(str(priv_path))

    with open(priv_path) as f:
        priv = f.read()
    with open(pub_path) as f:
        pub = f.read()
    return PythonRSASigner(pub, priv)


async def adb_connect(device_id: str, host: str, port: int = 5555) -> Optional[AdbDeviceTcpAsync]:
    signer = load_or_generate_adb_keys(device_id)
    device = AdbDeviceTcpAsync(host, port, default_transport_timeout_s=9.0)

    try:
        await device.connect(rsa_keys=[signer], auth_timeout_s=20)
        return device
    except Exception as e:
        print(f"ADB connection failed to {host}:{port} — {e}")
        return None

async def get_installed_apps(device: AdbDeviceTcpAsync) -> Dict[str, Dict[str, str]]:
    """Retrieve list of installed non-system apps in structured format."""
    output = await device.shell("pm list packages -3 -e")
    packages = sorted(line.replace("package:", "").strip() for line in output.splitlines())
    return {
        package: {"url": f"market://launch?id={package}"}
        for package in packages
    }

async def is_authorised(device: AdbDeviceTcpAsync) -> bool:
    try:
        result = await device.shell("echo ADB_OK")
        return "ADB_OK" in result
    except Exception:
        return False

async def test_connection(device_id: str, host: str) -> None:
    device = await adb_connect(device_id, host)
    if not device:
        return

    if await is_authorised(device):
        print("ADB authorisation confirmed.")
        print("Current app:", await get_current_app(device))
        print("Installed apps:", await get_installed_apps(device))
    else:
        print("Device not authorised. Please check the TV for an ADB prompt.")

    await device.close()


if __name__ == "__main__":
    import sys
    if len(sys.argv) != 3:
        print("Usage: python adb_tv.py <device_id> <host>")
    else:
        asyncio.run(test_connection(sys.argv[1], sys.argv[2]))
