"""Android TV device discovery with mDNS."""

import asyncio
import logging

from zeroconf import ServiceStateChange, Zeroconf
from zeroconf.asyncio import AsyncServiceBrowser, AsyncServiceInfo, AsyncZeroconf

_LOG = logging.getLogger(__name__)


async def android_tvs(timeout: int = 10) -> list[dict[str, str]]:
    """
    Discover Android TV devices with mDNS.

    :param timeout: discovery timeout in seconds.
    :return: dictionary containing name, label, address
    """
    discovered_android_tvs: list[dict[str, str]] = []

    def on_service_state_changed(
        zeroconf: Zeroconf, service_type: str, name: str, state_change: ServiceStateChange
    ) -> None:
        if state_change is not ServiceStateChange.Added:
            return

        _LOG.info("Found service: %s, %s", service_type, name)
        _ = asyncio.ensure_future(display_service_info(zeroconf, service_type, name))

    async def display_service_info(zeroconf: Zeroconf, service_type: str, name: str) -> None:
        info = AsyncServiceInfo(service_type, name)
        await info.async_request(zeroconf, 3000)

        if info:
            name_final = name
            name_str = name.split(".")
            if name_str:
                name_final = name_str[0]

            addresses = info.parsed_scoped_addresses()
            if addresses:
                discovered_tv = {"name": name_final, "label": f"{name_final} [{addresses[0]}]", "address": addresses[0]}
                discovered_android_tvs.append(discovered_tv)
        else:
            _LOG.debug("No info for %s", name)

    try:
        _LOG.debug("Discovering Android TV Remote Services")
        # warning: this can throw `OSError: [Errno 19] No such device` if the interface is not ready yet
        aiozc = AsyncZeroconf()
        services = ["_androidtvremote2._tcp.local."]

        aiobrowser = AsyncServiceBrowser(aiozc.zeroconf, services, handlers=[on_service_state_changed])

        await asyncio.sleep(timeout)
        await aiobrowser.async_cancel()
        await aiozc.async_close()
        _LOG.debug("Discovery finished")
    except OSError as ex:
        _LOG.error("Failed to start discovery: %s", ex)
    return discovered_android_tvs
