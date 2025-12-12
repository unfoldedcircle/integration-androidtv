import os
import unittest
from unittest.mock import patch, mock_open

from src.profiles import DeviceProfile, Profile, KeyPress


class TestDeviceProfile(unittest.TestCase):
    def setUp(self):
        self.device_profile = DeviceProfile()

    @patch(
        "builtins.open",
        new_callable=mock_open,
        read_data='{"manufacturer": "test", "model": "test_model", "features": ["ON_OFF"], "simple_commands": [], "command_map": {}}',
    )
    @patch("src.profiles.glob.glob")
    def test_load_single_valid_profile(self, mock_glob, mock_file):
        mock_glob.return_value = ["test_profile.json"]

        self.device_profile.load("/path")

        self.assertEqual(len(self.device_profile._profiles), 1)
        self.assertEqual(self.device_profile._profiles[0].manufacturer, "test")
        self.assertEqual(self.device_profile._profiles[0].model, "test_model")

    @patch(
        "builtins.open",
        new_callable=mock_open,
        read_data='{"manufacturer": "test", "model": "test_model", "features": ["ON_OFF"], "simple_commands": [], "command_map": {}}',
    )
    @patch("src.profiles.glob.glob")
    def test_load_default_profile_replacement(self, mock_glob, mock_file):
        mock_glob.return_value = ["test_profile.json"]

        self.device_profile.load("/path")

        self.assertEqual(self.device_profile._default_profile.manufacturer, "default")
        self.assertNotEqual(self.device_profile._default_profile, self.device_profile._profiles[0])

    def test_match_default_when_no_profiles(self):
        profile = self.device_profile.match("unknown", "unknown", use_chromecast=False)

        self.assertEqual(profile.manufacturer, "default")
        self.assertEqual(profile.model, "")

    def test_copy_profile(self):
        original_profile = Profile(
            manufacturer="example",
            model="example_model",
            features=[],
            simple_commands=[],
            command_map={},
        )

        copied_profile = original_profile.__copy__()

        self.assertEqual(original_profile.manufacturer, copied_profile.manufacturer)
        self.assertEqual(original_profile.model, copied_profile.model)
        self.assertNotEqual(id(original_profile.features), id(copied_profile.features))

    @patch(
        "builtins.open",
        new_callable=mock_open,
        read_data='{"manufacturer": "test", "model": "test_model", "features": ["ON_OFF"], "simple_commands": [], "command_map": {}}',
    )
    @patch("src.profiles.glob.glob")
    def test_load_ignores_invalid_files(self, mock_glob, mock_file):
        mock_glob.return_value = ["invalid_profile.json", "valid_profile.json"]
        mock_file.side_effect = [
            ValueError("Invalid JSON"),
            mock_open(
                read_data='{"manufacturer": "test", "model": "test_model", "features": ["ON_OFF"], "simple_commands": [], "command_map": {}}',
            ).return_value,
        ]

        self.device_profile.load("/path")

        self.assertEqual(len(self.device_profile._profiles), 1)
        self.assertEqual(self.device_profile._profiles[0].manufacturer, "test")


class TestProfileCommand(unittest.TestCase):
    def setUp(self):
        # Load the real default profile from config/profiles/default.json
        self.device_profile = DeviceProfile()
        path = os.path.dirname(os.path.dirname(__file__))
        profiles_dir = os.path.join(path, "config", "profiles")
        self.device_profile.load(profiles_dir)
        # Get the default profile (there's no specific unit test profile)
        self.profile = self.device_profile.match("unit", "test", use_chromecast=False)

    def test_command_mapped_from_profile_long_action(self):
        # "HOME_LONG" is mapped in default.json with action LONG
        cmd = self.profile.command("HOME_LONG")
        self.assertIsNotNone(cmd)
        self.assertEqual(cmd.keycode, "HOME")
        self.assertEqual(cmd.action, KeyPress.LONG)

    def test_command_media_player_mapping(self):
        # Should map through MEDIA_PLAYER_COMMANDS via ucapi media_player.Commands
        cmd = self.profile.command("play_pause")
        self.assertIsNotNone(cmd)
        # Expect Android TV key name for play/pause
        self.assertEqual(cmd.keycode, "MEDIA_PLAY_PAUSE")
        self.assertEqual(cmd.action, KeyPress.SHORT)

    def test_command_keycode_literal_passthrough(self):
        # Not in command_map, but starts with KEYCODE_ so should pass through as-is
        cmd = self.profile.command("KEYCODE_STAR")
        self.assertIsNotNone(cmd)
        self.assertEqual(cmd.keycode, "KEYCODE_STAR")
        self.assertEqual(cmd.action, KeyPress.SHORT)

    def test_command_numeric_keycode(self):
        cmd = self.profile.command("123")
        self.assertIsNotNone(cmd)
        self.assertEqual(cmd.keycode, 123)
        self.assertEqual(cmd.action, KeyPress.SHORT)

    def test_command_unknown_returns_none(self):
        cmd = self.profile.command("not_a_command")
        self.assertIsNone(cmd)


if __name__ == "__main__":
    unittest.main()
