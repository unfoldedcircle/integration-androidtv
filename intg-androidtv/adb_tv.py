import asyncio
import os
import re
from typing import List

from adb_shell.adb_device_async import AdbDeviceTcpAsync
from adb_shell.auth.keygen import keygen
from adb_shell.auth.sign_pythonrsa import PythonRSASigner

# Key file paths
ADBKEY = "./adbkey"
ADBKEY_PUB = "./adbkey.pub"

# Android TV IP address
HOST = "192.168.0.250"
PORT = 5555


def load_or_generate_adb_keys() -> PythonRSASigner:
    """Ensure ADB RSA keys exist and return the signer."""
    if not os.path.exists(ADBKEY) or not os.path.exists(ADBKEY_PUB):
        print("🔑 ADB keys not found, generating new keys...")
        keygen(ADBKEY)
        print("✅ Keys generated.")

    with open(ADBKEY) as f:
        priv = f.read()
    with open(ADBKEY_PUB) as f:
        pub = f.read()
    return PythonRSASigner(pub, priv)


async def get_installed_apps(device: AdbDeviceTcpAsync) -> List[str]:
    """Retrieve list of installed apps."""
    output = await device.shell("pm list packages")
    return sorted(line.replace("package:", "").strip() for line in output.splitlines())


async def get_current_app(device: AdbDeviceTcpAsync) -> str | None:
    """Return the currently focused app (foreground)."""
    output = await device.shell(
        "dumpsys window windows | grep -E 'mCurrentFocus|mFocusedApp'"
    )
    match = re.search(r"([a-zA-Z0-9_.]+)/[a-zA-Z0-9_.]+", output)
    return match.group(1) if match else None


async def connect_and_run():
    signer = load_or_generate_adb_keys()
    device = AdbDeviceTcpAsync(HOST, PORT, default_transport_timeout_s=9.0)

    print(f"🔌 Connecting to {HOST}:{PORT}...")
    try:
        await device.connect(rsa_keys=[signer], auth_timeout_s=10)
        print("✅ Connected successfully.")

        # Test shell command
        output = await device.shell("echo Hello from Android TV")
        print(f"🟢 Shell output:\n{output.strip()}")

        # Get current foreground app
        current_app = await get_current_app(device)
        print(f"📱 Current app: {current_app or 'Unknown'}")

        # List installed packages
        apps = await get_installed_apps(device)
        print(f"📦 Installed apps ({len(apps)} total):")
        for app in apps:  # Show top 10
            print(f"{app}")

    except Exception as e:
        print(f"❌ Error: {e}")
    finally:
        await device.close()


if __name__ == "__main__":
    asyncio.run(connect_and_run())
