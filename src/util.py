"""
Utility functions.

:copyright: (c) 2025 by Unfolded Circle ApS.
:license: MPL-2.0, see LICENSE for more details.
"""

from copy import deepcopy
from typing import Any

from ucapi.media_player import Attributes as MediaAttr


def filter_data_img_properties(data: dict[str, Any] | None) -> dict[str, Any]:
    """
    Filter base64 encoded image fields for log messages in the given msg data dict.

    The input dictionary is not modified, all filtered fields are returned in a new dictionary.

    - Filtered attributes in `data`: `media_image_url`, `icon`
    - `msg_data` dict and list items:
        - `msg_data.attributes.media_image_url`
        - `msg_data[].attributes.media_image_url`

    :param data: the message data dict
    :return: copy of the message data dict with filtered attributes
    """
    # do not modify the original dict
    log_upd = deepcopy(data)
    if not log_upd:
        return {}

    if "icon" in log_upd and log_upd["icon"].startswith("data:"):
        log_upd["icon"] = "data:***"
    if MediaAttr.MEDIA_IMAGE_URL in log_upd and log_upd[MediaAttr.MEDIA_IMAGE_URL].startswith("data:"):
        log_upd[MediaAttr.MEDIA_IMAGE_URL] = "data:***"

    if "msg_data" in log_upd:
        if (
            "attributes" in log_upd["msg_data"]
            and MediaAttr.MEDIA_IMAGE_URL in log_upd["msg_data"]["attributes"]
            and log_upd["msg_data"]["attributes"][MediaAttr.MEDIA_IMAGE_URL].startswith("data:")
        ):
            log_upd["msg_data"]["attributes"][MediaAttr.MEDIA_IMAGE_URL] = "data:***"
        elif isinstance(log_upd["msg_data"], list):
            for item in log_upd["msg_data"]:
                if (
                    "attributes" in item
                    and MediaAttr.MEDIA_IMAGE_URL in item["attributes"]
                    and item["attributes"][MediaAttr.MEDIA_IMAGE_URL].startswith("data:")
                ):
                    item["attributes"][MediaAttr.MEDIA_IMAGE_URL] = "data:***"

    return log_upd
