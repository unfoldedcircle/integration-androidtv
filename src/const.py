"""
Android TV constants.

:copyright: (c) 2023-2025 by Unfolded Circle ApS.
:license: MPL-2.0, see LICENSE for more details.
"""

from ucapi.media_player import Commands

# pylint: disable=C0301
from ucapi.ui import Buttons, DeviceButtonMapping, EntityCommand, UiPage

REMOTE_BUTTONS_MAPPING: list[DeviceButtonMapping] = [
    DeviceButtonMapping(button=Buttons.BACK, short_press=EntityCommand(**{"cmd_id": Commands.BACK})),
    DeviceButtonMapping(button=Buttons.HOME, short_press=EntityCommand(**{"cmd_id": Commands.HOME})),
    DeviceButtonMapping(button=Buttons.CHANNEL_DOWN, short_press=EntityCommand(**{"cmd_id": Commands.CHANNEL_DOWN})),
    DeviceButtonMapping(button=Buttons.CHANNEL_UP, short_press=EntityCommand(**{"cmd_id": Commands.CHANNEL_UP})),
    DeviceButtonMapping(button=Buttons.DPAD_UP, short_press=EntityCommand(**{"cmd_id": Commands.CURSOR_UP})),
    DeviceButtonMapping(button=Buttons.DPAD_DOWN, short_press=EntityCommand(**{"cmd_id": Commands.CURSOR_DOWN})),
    DeviceButtonMapping(button=Buttons.DPAD_LEFT, short_press=EntityCommand(**{"cmd_id": Commands.CURSOR_LEFT})),
    DeviceButtonMapping(button=Buttons.DPAD_RIGHT, short_press=EntityCommand(**{"cmd_id": Commands.CURSOR_RIGHT})),
    DeviceButtonMapping(button=Buttons.DPAD_MIDDLE, short_press=EntityCommand(**{"cmd_id": Commands.CURSOR_ENTER})),
    DeviceButtonMapping(button=Buttons.PLAY, short_press=EntityCommand(**{"cmd_id": Commands.PLAY_PAUSE})),
    DeviceButtonMapping(button=Buttons.PREV, short_press=EntityCommand(**{"cmd_id": Commands.PREVIOUS})),
    DeviceButtonMapping(button=Buttons.NEXT, short_press=EntityCommand(**{"cmd_id": Commands.NEXT})),
    DeviceButtonMapping(button=Buttons.VOLUME_UP, short_press=EntityCommand(**{"cmd_id": Commands.VOLUME_UP})),
    DeviceButtonMapping(button=Buttons.VOLUME_DOWN, short_press=EntityCommand(**{"cmd_id": Commands.VOLUME_DOWN})),
    DeviceButtonMapping(button=Buttons.MUTE, short_press=EntityCommand(**{"cmd_id": Commands.MUTE_TOGGLE})),
    DeviceButtonMapping(button=Buttons.STOP, short_press=EntityCommand(**{"cmd_id": Commands.STOP})),
    DeviceButtonMapping(button=Buttons.MENU, short_press=EntityCommand(**{"cmd_id": Commands.CONTEXT_MENU})),
]


REMOTE_UI_PAGES: list[UiPage] = [
    UiPage(
        **{
            "page_id": "Android TV numbers",
            "name": "Android TV numbers",
            "grid": {"height": 4, "width": 3},
            "items": [
                {
                    "command": {"cmd_id": "remote.send", "params": {"command": Commands.DIGIT_1}},
                    "location": {"x": 0, "y": 0},
                    "text": "1",
                    "type": "text",
                },
                {
                    "command": {"cmd_id": "remote.send", "params": {"command": Commands.DIGIT_2}},
                    "location": {"x": 1, "y": 0},
                    "text": "2",
                    "type": "text",
                },
                {
                    "command": {"cmd_id": "remote.send", "params": {"command": Commands.DIGIT_3}},
                    "location": {"x": 2, "y": 0},
                    "text": "3",
                    "type": "text",
                },
                {
                    "command": {"cmd_id": "remote.send", "params": {"command": Commands.DIGIT_4}},
                    "location": {"x": 0, "y": 1},
                    "text": "4",
                    "type": "text",
                },
                {
                    "command": {"cmd_id": "remote.send", "params": {"command": Commands.DIGIT_5}},
                    "location": {"x": 1, "y": 1},
                    "text": "5",
                    "type": "text",
                },
                {
                    "command": {"cmd_id": "remote.send", "params": {"command": Commands.DIGIT_6}},
                    "location": {"x": 2, "y": 1},
                    "text": "6",
                    "type": "text",
                },
                {
                    "command": {"cmd_id": "remote.send", "params": {"command": Commands.DIGIT_7}},
                    "location": {"x": 0, "y": 2},
                    "text": "7",
                    "type": "text",
                },
                {
                    "command": {"cmd_id": "remote.send", "params": {"command": Commands.DIGIT_8}},
                    "location": {"x": 1, "y": 2},
                    "text": "8",
                    "type": "text",
                },
                {
                    "command": {"cmd_id": "remote.send", "params": {"command": Commands.DIGIT_9}},
                    "location": {"x": 2, "y": 2},
                    "text": "9",
                    "type": "text",
                },
                {
                    "command": {"cmd_id": "remote.send", "params": {"command": Commands.DIGIT_0}},
                    "location": {"x": 1, "y": 3},
                    "text": "0",
                    "type": "text",
                },
            ],
        }
    ),
]
