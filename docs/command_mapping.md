# Command Mapping

The following feature set is defined for the exposed
[Remote Two/3 media-player entity](https://github.com/unfoldedcircle/core-api/blob/main/doc/entities/entity_media_player.md)
in the default device profile: 

| Feature          | Command(s)                                                            | Android remote keycode(s)                                    | Comments                                                                                                                                                                                                        |
|------------------|-----------------------------------------------------------------------|--------------------------------------------------------------|-----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|
| on_off           | on, off                                                               | POWER                                                        | No dedicated on- and off-commands. State is handled in driver.                                                                                                                                                  |
| toggle           | toggle                                                                | POWER                                                        |                                                                                                                                                                                                                 |
| volume_up_down   | volume_up, volume_down                                                | VOLUME_UP, VOLUME_DOWN                                       |                                                                                                                                                                                                                 |
| mute_toggle      | mute_toggle                                                           | VOLUME_MUTE                                                  |                                                                                                                                                                                                                 |
| play_pause       | play_pause                                                            | MEDIA_PLAY_PAUSE                                             |                                                                                                                                                                                                                 |
| stop             | stop                                                                  | MEDIA_STOP                                                   |                                                                                                                                                                                                                 |
| next             | next                                                                  | MEDIA_NEXT                                                   |                                                                                                                                                                                                                 |
| previous         | previous                                                              | MEDIA_PREVIOUS                                               |                                                                                                                                                                                                                 |
| fast_forward     | fast_forward                                                          | MEDIA_FAST_FORWARD                                           |                                                                                                                                                                                                                 |
| rewind           | rewind                                                                | MEDIA_REWIND                                                 |                                                                                                                                                                                                                 |
| media_duration   | -                                                                     | -                                                            | Only with Google Cast enabled and supported applications.                                                                                                                                                       |
| media_position   | -                                                                     | -                                                            | Only with Google Cast enabled and supported applications.                                                                                                                                                       |
| media_title      | -                                                                     | -                                                            | Returned attribute, usually the running application ID or friendly name if available. With Google Cast enabled: media title if supported.                                                                       |
| media_artist     | -                                                                     | -                                                            | Only with Google Cast enabled and supported applications.                                                                                                                                                       |
| media_album      | -                                                                     | -                                                            | Only with Google Cast enabled and supported applications.                                                                                                                                                       |
| media_image_url  | -                                                                     | -                                                            | Only with Google Cast enabled and supported applications.                                                                                                                                                       |
| media_type       | -                                                                     | -                                                            | Only with Google Cast enabled and supported applications.                                                                                                                                                       |
| dpad             | cursor_up, cursor_down,<br>cursor_left, cursor_right,<br>cursor_enter | DPAD_UP, DPAD_DOWN,<br>DPAD_LEFT, DPAD_RIGHT,<br>DPAD_CENTER |                                                                                                                                                                                                                 |
| numpad           | digit_0 ... digit_9                                                   | 0 ... 9                                                      |                                                                                                                                                                                                                 |
| home             | home, back                                                            | HOME, BACK                                                   |                                                                                                                                                                                                                 |
| menu             | menu, back                                                            | MENU, BACK                                                   | Alternative might be TV_CONTENTS_MENU, TV_MEDIA_CONTEXT_MENU                                                                                                                                                    |
| context_menu     | context_menu                                                          | TV_MEDIA_CONTEXT_MENU                                        | On Chromecast & Shield mapped to DPAD_CENTER long-press                                                                                                                                                         |
| guide            | guide, back                                                           | GUIDE, BACK                                                  |                                                                                                                                                                                                                 |
| info             | info, back                                                            | INFO                                                         |                                                                                                                                                                                                                 |
| color_buttons    | function_red, function_green,<br>function_yellow, function_blue       | PROG_RED, PROG_GREEN,<br>PROG_YELLOW, PROG_BLUE              |                                                                                                                                                                                                                 |
| channel_switcher | channel_up, channel_down                                              | CHANNEL_UP, CHANNEL_DOWN                                     |                                                                                                                                                                                                                 |
| select_source    | select_source                                                         | -                                                            | Launch application from a predefined list (see [apps.py](../intg-androidtv/apps.py)).<br>Switching TV inputs with `TV_INPUT_*` keycodes doesn't seem to work on most TVs (negative feedback for Philips, Sony). |
| eject            | eject                                                                 | MEDIA_EJECT                                                  |                                                                                                                                                                                                                 |
| open_close       | open_close                                                            | MEDIA_CLOSE                                                  |                                                                                                                                                                                                                 |
| audio_track      | audio_track                                                           | MEDIA_AUDIO_TRACK                                            |                                                                                                                                                                                                                 |
| subtitle         | subtitle                                                              | CAPTIONS                                                     |                                                                                                                                                                                                                 |
| record           | record                                                                | MEDIA_RECORD                                                 |                                                                                                                                                                                                                 |
| settings         | settings                                                              | SETTINGS                                                     | Profile mapping for Chromecast: MENU long-press, Shield: BACK long-press                                                                                                                                        |
| search           | search                                                                | SEARCH                                                       | Limited usability without keyboard or voice input                                                                                                                                                               |
| seek             | seek                                                                  | -                                                            | Only available with Google Cast.                                                                                                                                                                                |

- Available Android remote keycodes are defined in: https://github.com/tronikos/androidtvremote2/blob/v0.0.14/src/androidtvremote2/remotemessage.proto#L90

## Device Profiles

Unfortunately, the keycode support of Android TV devices is very limited and device- (and probably even app-) specific.

Device profiles allow better support of the available features and different key-mappings:

- Definition of available [media-player features](https://github.com/unfoldedcircle/core-api/blob/main/doc/entities/entity_media_player.md#features).
- Definition of additional simple commands, which either specify a keycode or map a command:
  - Android remote keycode name, starting with `KEYCODE_`, for example: `"KEYCODE_F12"`.
  - Android remote keycode as string, for example: `"142"`. This is intended for testing, see below.
  - Uppercase name which maps to a keycode and optional key action (`SHORT`, `LONG`, `DOUBLE_CLICK`).

Available profiles:

- Google Chromecast with Google TV, HD and 4k: [Google_Chromecast.json](../config/profiles/Google_Chromecast.json)
- NVIDIA SHIELD TV (Pro): [NVIDIA_SHIELD.json](../config/profiles/NVIDIA_SHIELD.json)
- Default, if no profile matches: [default.json](../config/profiles/default.json)

Pull requests for additional devices are greatly appreciated. 

### Device Profile Matching

Profiles are matched based on the manufacturer name and device model, which is returned in the
[device information](https://github.com/tronikos/androidtvremote2/blob/v0.0.14/src/androidtvremote2/androidtv_remote.py#L101)
from the androidtvremote2 library.

Each device profile is defined in a separate JSON file in [config/profiles](../config/profiles). All profiles are read
during driver startup and sorted alphabetically.

- Manufacturer and model fields in the profile file are treated as prefixes.
- Matching against the returned information from the device is performed case-insensitive.
- Manufacturer is mandatory, model is optional (empty field).

For example, profile `manufacturer = "foo"`, `model = "bar"` will match the following device information:

- foo / bar
- FOO / Bart
- Foot / Barista


## Testing Keycodes

To simplify testing and identifying working keycodes, the [available keycodes](https://github.com/tronikos/androidtvremote2/blob/v0.0.14/src/androidtvremote2/remotemessage.proto#L90)
can be sent directly with the Core-API to the Remote Two/3 or Core Simulator.
Either as name (including the `KEYCODE_` prefix), or as numeric value string.

Example:
```console
curl --location --request PUT '$IP/api/entities/uc_androidtv_driver.main.012345678912/command' \
--header 'Content-Type: application/json' \
-u web-configurator:$PIN \
--data '{"cmd_id": "KEYCODE_TAB"}'
```

With numeric keycode: `{"cmd_id": "61"}`
