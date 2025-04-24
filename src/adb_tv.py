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
    output = await device.shell("pm list packages -3 -e")
    return sorted(line.replace("package:", "").strip() for line in output.splitlines())


async def get_current_app(device: AdbDeviceTcpAsync) -> str | None:
    """Return the currently focused app (foreground)."""
    output = await device.shell("dumpsys window | grep mCurrentFocus")
    match = re.search(r"([a-zA-Z0-9_.]+)/[a-zA-Z0-9_.]+", output)
    return match.group(1) if match else None


async def get_media_info(device):
    output = await device.shell("dumpsys media_session")
    sessions = output.split("Sessions Stack")
    if len(sessions) < 2:
        return None

    media_info = {}
    # Find the top (active) session
    active_session = sessions[1]

    title = re.search(r"title=(.*)", active_session)
    artist = re.search(r"artist=(.*)", active_session)
    state = re.search(r"state=(\d+)", active_session)

    if title:
        media_info["title"] = title.group(1).strip()
    if artist:
        media_info["artist"] = artist.group(1).strip()
    if state:
        media_info["state"] = int(state.group(1).strip())

    return media_info or None


async def get_media_metadata(device):
    output = await device.shell("dumpsys media_session")

    # Naive parsing (for proof of concept)
    current_entry = {}
    lines = output.splitlines()
    in_metadata = False
    for line in lines:
        line = line.strip()

        if "MediaSessionRecord" in line:
            current_entry = {}

        if "package=" in line:
            current_entry["package"] = line.split("package=")[-1]

        if "state=PlaybackState" in line:
            current_entry["state"] = line

        if "metadata=MediaMetadata" in line:
            in_metadata = True

        elif in_metadata:
            if "=" in line:
                key, val = map(str.strip, line.split("=", 1))
                current_entry[key] = val
            elif line == "}":
                in_metadata = False
                break

    return current_entry


async def connect_and_run():
    signer = load_or_generate_adb_keys()
    device = AdbDeviceTcpAsync(HOST, PORT, default_transport_timeout_s=9.0)

    print(f"Connecting to {HOST}:{PORT}...")
    try:
        await device.connect(rsa_keys=[signer], auth_timeout_s=10)
        print("Connected successfully.")

        # Test shell command
        output = await device.shell("echo Hello from Android TV")
        print(f"Shell output:\n{output.strip()}")

        # Get current foreground app
        current_app = await get_current_app(device)
        print(f"Current app: {current_app or 'Unknown'}")

        print(await device.shell("getprop ro.product.model"))
        print(await device.shell("getprop ro.product.manufacturer"))

        # print(await device.shell("dumpsys activity activities | grep mResumedActivity"))
        # print(await device.shell("dumpsys activity recents | grep RecentTaskInfo"))
        print(await device.shell("input keyevent KEYCODE_A"))
        # keycode for the letter A

        # List installed packages
        # apps = await get_installed_apps(device)
        # print(f"📦 Installed apps ({len(apps)} total):")
        # for app in apps:
        #     print(f"{app}")

        # print(await get_media_metadata(device))
        # media_output = await get_media_info(device)
        # if media_output:
        #     print("🎵 Media Info:")
        #     print(f"Title: {media_output.get('title', 'Unknown')}")
        #     print(f"Artist: {media_output.get('artist', 'Unknown')}")
        #     print(f"State: {media_output.get('state', 'Unknown')}")
        # else:
        #     print("🎵 No media session found.")

    except Exception as e:
        print(f"Error: {e}")
    finally:
        await device.close()


if __name__ == "__main__":
    asyncio.run(connect_and_run())
