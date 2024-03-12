"""
Device profile management for Android TV Remote integration.

Each manufacturer uses different commands or patterns to call certain functions...

:copyright: (c) 2024 by Unfolded Circle ApS.
:license: MPL-2.0, see LICENSE for more details.
"""

import glob
import json
import logging
from dataclasses import dataclass
from enum import IntEnum

from ucapi import media_player

_LOG = logging.getLogger(__name__)


# Map Remote Two media-player entity commands to Android TV key codes (KEYCODE_ prefix can be omitted)
# See https://github.com/tronikos/androidtvremote2/blob/v0.0.14/src/androidtvremote2/remotemessage.proto
MEDIA_PLAYER_COMMANDS = {
    media_player.Commands.ON.value: "POWER",
    media_player.Commands.OFF.value: "POWER",
    media_player.Commands.PLAY_PAUSE.value: "MEDIA_PLAY_PAUSE",
    media_player.Commands.STOP.value: "MEDIA_STOP",
    media_player.Commands.PREVIOUS.value: "MEDIA_PREVIOUS",
    media_player.Commands.NEXT.value: "MEDIA_NEXT",
    media_player.Commands.FAST_FORWARD.value: "MEDIA_FAST_FORWARD",
    media_player.Commands.REWIND.value: "MEDIA_REWIND",
    media_player.Commands.VOLUME_UP.value: "VOLUME_UP",
    media_player.Commands.VOLUME_DOWN.value: "VOLUME_DOWN",
    media_player.Commands.MUTE_TOGGLE.value: "VOLUME_MUTE",
    media_player.Commands.CHANNEL_UP.value: "CHANNEL_UP",
    media_player.Commands.CHANNEL_DOWN.value: "CHANNEL_DOWN",
    media_player.Commands.CURSOR_UP.value: "DPAD_UP",
    media_player.Commands.CURSOR_DOWN.value: "DPAD_DOWN",
    media_player.Commands.CURSOR_LEFT.value: "DPAD_LEFT",
    media_player.Commands.CURSOR_RIGHT.value: "DPAD_RIGHT",
    media_player.Commands.CURSOR_ENTER.value: "DPAD_CENTER",
    media_player.Commands.FUNCTION_RED.value: "PROG_RED",
    media_player.Commands.FUNCTION_GREEN.value: "PROG_GREEN",
    media_player.Commands.FUNCTION_YELLOW.value: "PROG_YELLOW",
    media_player.Commands.FUNCTION_BLUE.value: "PROG_BLUE",
    media_player.Commands.HOME.value: "HOME",
    media_player.Commands.MENU.value: "MENU",  # alternatives: KEYCODE_TV_CONTENTS_MENU  KEYCODE_TV_MEDIA_CONTEXT_MENU
    media_player.Commands.CONTEXT_MENU.value: "TV_MEDIA_CONTEXT_MENU",
    media_player.Commands.GUIDE.value: "GUIDE",
    media_player.Commands.INFO.value: "INFO",
    media_player.Commands.BACK.value: "BACK",
    media_player.Commands.DIGIT_0.value: "0",
    media_player.Commands.DIGIT_1.value: "1",
    media_player.Commands.DIGIT_2.value: "2",
    media_player.Commands.DIGIT_3.value: "3",
    media_player.Commands.DIGIT_4.value: "4",
    media_player.Commands.DIGIT_5.value: "5",
    media_player.Commands.DIGIT_6.value: "6",
    media_player.Commands.DIGIT_7.value: "7",
    media_player.Commands.DIGIT_8.value: "8",
    media_player.Commands.DIGIT_9.value: "9",
    media_player.Commands.RECORD.value: "MEDIA_RECORD",
    media_player.Commands.MY_RECORDINGS.value: "DVR",
    media_player.Commands.LIVE.value: "TV",
    media_player.Commands.EJECT.value: "MEDIA_EJECT",
    media_player.Commands.OPEN_CLOSE.value: "MEDIA_CLOSE",
    media_player.Commands.AUDIO_TRACK.value: "MEDIA_AUDIO_TRACK",
    media_player.Commands.SUBTITLE.value: "CAPTIONS",
    media_player.Commands.SETTINGS.value: "SETTINGS",
    media_player.Commands.SEARCH.value: "SEARCH",
}


class KeyPress(IntEnum):
    """Key press actions."""

    SHORT = 0
    LONG = 1
    DOUBLE_CLICK = 2
    BEGIN = 3
    END = 4


@dataclass
class Command:
    """Device command."""

    keycode: str | int
    """int (e.g. 26) or str (e.g. "KEYCODE_POWER" or just "POWER")"""
    action: KeyPress = KeyPress.SHORT
    """Key press action"""


@dataclass
class Profile:
    """Device profile data."""

    manufacturer: str
    """Mandatory device manufacturer (prefix)"""
    model: str
    """Optional device model (prefix)"""
    features: list[media_player.Features]
    simple_commands: list[str]
    command_map: dict[str, Command]

    def command(self, cmd_id: str) -> Command | None:
        """
        Retrieve matching command for a command identifier.

        :param cmd_id: command identifier
        :return: matching Command or None if not found
        """
        # test first if it's a mapped command.
        command = self.command_map.get(cmd_id)
        if command:
            return command
        # media-player command?
        try:
            command = media_player.Commands[cmd_id.upper()]
            if command.value in MEDIA_PLAYER_COMMANDS:
                return Command(MEDIA_PLAYER_COMMANDS[command.value])
        except KeyError:
            pass
        # key-code? This is intended for testing
        if cmd_id.startswith("KEYCODE_"):
            return Command(cmd_id)
        if cmd_id.isnumeric():
            return Command(int(cmd_id))

        return None


class DeviceProfile:
    """Device profile handling."""

    def __init__(self):
        """Create instance."""
        self._profiles: list[Profile] = []
        self._default_profile = Profile(
            "default",
            "",
            [
                media_player.Features.ON_OFF,
                media_player.Features.TOGGLE,
                media_player.Features.VOLUME,
                media_player.Features.VOLUME_UP_DOWN,
                media_player.Features.MUTE_TOGGLE,
                media_player.Features.PLAY_PAUSE,
                media_player.Features.NEXT,
                media_player.Features.PREVIOUS,
                media_player.Features.HOME,
                media_player.Features.MENU,
                media_player.Features.CHANNEL_SWITCHER,
                media_player.Features.DPAD,
                media_player.Features.SELECT_SOURCE,
                media_player.Features.MEDIA_TITLE,
                media_player.Features.COLOR_BUTTONS,
                media_player.Features.FAST_FORWARD,
                media_player.Features.REWIND,
                media_player.Features.NUMPAD,
                media_player.Features.GUIDE,
                media_player.Features.INFO,
                media_player.Features.EJECT,
                media_player.Features.OPEN_CLOSE,
                media_player.Features.AUDIO_TRACK,
                media_player.Features.SUBTITLE,
                media_player.Features.RECORD,
            ],
            [],
            {},
        )

    def load(self, path: str):
        """
        Load device profile files from given path.

        :param path: file path of device profile files
        """
        self._profiles = []
        files = sorted(glob.glob(path + "/*.json"), key=str.swapcase)
        for file in files:
            _LOG.debug("Loading device profile file: %s", file)

            try:
                with open(file, "r", encoding="utf-8") as f:
                    # Best effort loading of device profile file. Ignore non-valid data.
                    data = json.load(f)
                    # There's probably a more pythonic way to load enums, they are just confusing to non-Python devs...
                    profile = Profile(
                        data["manufacturer"],
                        data["model"],
                        _convert_features(data["features"]),
                        data["simple_commands"],
                        _convert_command_map(data["command_map"]),
                    )
                    if profile.manufacturer == "default":
                        self._default_profile = profile
                    self._profiles.append(profile)
            except Exception as ex:  # pylint: disable=broad-exception-caught
                _LOG.error("Error loading device profile file %s: %s", file, ex)
        _LOG.debug("Loaded profiles: %s", self._profiles)

    def match(self, manufacturer: str, model: str) -> Profile:
        """
        Get a matching device profile for the given manufacturer and model.

        Matching a device profile is performed case-insensitive and the manufacturer and model parameters are treated
        as prefixes.

        For example: parameters `manufacturer = "foo"`, `model = "bar"` will match:

        - foo / bar
        - FOO / Bar
        - Foot / Barista

        :param manufacturer: mandatory manufacturer prefix
        :param model: optional model name prefix, ignored if empty
        :return: matching device profil or default profile if no match
        """
        for profile in self._profiles:
            if manufacturer.upper().startswith(profile.manufacturer.upper()):
                if profile.model:
                    if model.upper().startswith(profile.model.upper()):
                        return profile
                    continue
                return profile
        _LOG.info("No matching device profile found for %s %s: using default profile", manufacturer, model)
        return self._default_profile


def _convert_features(values: list[str]) -> list[media_player.Features]:
    features = []
    for value in values:
        if feature := _str_to_feature(value):
            features.append(feature)
    return features


def _str_to_feature(value: str) -> media_player.Features | None:
    try:
        return getattr(media_player.Features, value.upper())
    except AttributeError:
        return None


def _convert_command_map(values: dict[str, any]) -> dict[str, Command]:
    cmd_map = {}
    for key, value in values.items():
        try:
            action = getattr(KeyPress, value["action"]) if "action" in value else KeyPress.SHORT
            command = Command(value["keycode"], action)
            cmd_map[key] = command
        except Exception as ex:  # pylint: disable=broad-exception-caught
            _LOG.error("Invalid command map for %s: %s", key, ex)
    return cmd_map
