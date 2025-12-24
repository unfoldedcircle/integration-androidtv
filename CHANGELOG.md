# Android TV integration for Remote Two/3 Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## Unreleased
_Changes in the next release_

---

## v0.8.1 - 2025-12-24
### Added
- New FULL_SCREEN command in Dune HD Homatic Profile ([#125](https://github.com/unfoldedcircle/integration-androidtv/pull/125)).

### Changed
- Use journald log levels if running as a systemd service ([#126](https://github.com/unfoldedcircle/integration-androidtv/pull/126)).

## v0.8.0 - 2025-12-22
### Added
- Google voice commands ([#120](https://github.com/unfoldedcircle/integration-androidtv/pull/120)).

### Changed
- Optimize Plex artwork image loading ([#80](https://github.com/unfoldedcircle/integration-androidtv/issues/80), [#113](https://github.com/unfoldedcircle/integration-androidtv/issues/113)).

### Fixed
- Improve media information handling if Chromecast is enabled ([#124](https://github.com/unfoldedcircle/integration-androidtv/pull/124)).

## v0.7.7 - 2025-12-04
### Fixed
- Fixed infinite update of media image url update using chromecast. Contributed by @albaintor, thanks! ([#115](https://github.com/unfoldedcircle/integration-androidtv/pull/115)).

### Changed
- If a client disconnects, the connections to the Android TV devices are no longer closed ([#111](https://github.com/unfoldedcircle/integration-androidtv/pull/111)).

## v0.7.6 - 2025-11-21
### Fixed
- Potential reconnection failures in some scenarios. Contributed by @albaintor, thanks! ([#83](https://github.com/unfoldedcircle/integration-androidtv/pull/83)).
- Crash when Chromecast publishes an artwork. Contributed by @albaintor, thanks! ([#99](https://github.com/unfoldedcircle/integration-androidtv/pull/99)).

### Changed
- Update androidtvremote2 library to 0.3.0 ([#107](https://github.com/unfoldedcircle/integration-androidtv/pull/107)).
- Update pychromecast library to 14.0.9 ([#101](https://github.com/unfoldedcircle/integration-androidtv/pull/101)).
- Update ucapi to 0.3.2 ([#100](https://github.com/unfoldedcircle/integration-androidtv/pull/100)).
- CI: bump GitHub Actions to newer major versions: actions/checkout v6, actions/setup-node v6, actions/download-artifact v6, actions/upload-artifact v5 ([#110](https://github.com/unfoldedcircle/integration-androidtv/pull/110), [#103](https://github.com/unfoldedcircle/integration-androidtv/pull/103), [#105](https://github.com/unfoldedcircle/integration-androidtv/pull/105), [#106](https://github.com/unfoldedcircle/integration-androidtv/pull/106)).

## v0.7.5 - 2025-09-18
### Added
- Identify Projectivity launcher as a home screen app ([#89](https://github.com/unfoldedcircle/integration-androidtv/pull/89)).
### Changed
- onn. Streaming Device 4K pro: using the default settings key instead of a long home press to access the settings menu ([#88](https://github.com/unfoldedcircle/integration-androidtv/pull/88)).
- Update the embedded Python runtime to 3.11.13 ([#97](https://github.com/unfoldedcircle/integration-androidtv/pull/97)).
- Enabled GitHub dependabot.
### Fixed
- qemu installation instructions.

## v0.7.4 - 2025-05-15
### Fixed
- Sporadic media image URL replacement with an invalid URL ([#75](https://github.com/unfoldedcircle/integration-androidtv/pull/75)).

## v0.7.3 - 2025-05-13
### Added
- Configurable volume step when using Google Cast volume control. Contributed by @albaintor, thanks! ([#72](https://github.com/unfoldedcircle/integration-androidtv/pull/71))
### Fixed
- Normal volume control with Android TV keycodes (regression in v0.7.0) ([#72](https://github.com/unfoldedcircle/integration-androidtv/issues/72)).
### Changed
- Pre-select configure action in setup-flow if a device exists ([#73](https://github.com/unfoldedcircle/integration-androidtv/pull/73)).

## v0.7.2 - 2025-04-27
### Changed
- Sanitize metadata icon filename for file operations.

## v0.7.0 - 2025-04-26
### Added
- Google Cast for media info & seeking support. Contributed by @albaintor, thanks! ([#57](https://github.com/unfoldedcircle/integration-androidtv/pull/57))
  - This is currently a preview feature and must be enabled in the device configuration of the integration setup.
- External app name and icon metadata from Google Play. Contributed by @thomasm789, thanks! ([#60](https://github.com/unfoldedcircle/integration-androidtv/pull/60), [#67](https://github.com/unfoldedcircle/integration-androidtv/pull/67), [#66](https://github.com/unfoldedcircle/integration-androidtv/pull/66)).
- myCANAL application ([#55](https://github.com/unfoldedcircle/integration-androidtv/pull/55)).
- Set media player attribute "media_position_updated_at" ([feature-and-bug-tracker#443](https://github.com/unfoldedcircle/feature-and-bug-tracker/issues/443)).

### Changed
- Add a support article link and change the setup description in the first setup flow screen [#61](https://github.com/unfoldedcircle/integration-androidtv/pull/61).
- Update the embedded Python runtime to 3.11.12 and upgrade common Python libraries like zeroconf and websockets [#65](https://github.com/unfoldedcircle/integration-androidtv/pull/65)..
- Project structure refactoring and initial unit tests [#66](https://github.com/unfoldedcircle/integration-androidtv/pull/66).

## v0.6.3 - 2024-12-08
### Added
- Add device profile for "onn. Streaming Device 4K pro". 

## v0.6.2 - 2024-07-23
### Changed
- Create a one-folder bundle with pyinstaller instead a one-file bundle to save resources.
- Change archive format to the custom integration installation archive.
- Change default `driver_id` value in `driver.json` to create a compatible custom installation archive.

## v0.6.1 - 2024-07-21
### Fixed
- Add missing stop feature to Nvidia & Chromecast profiles ([#52](https://github.com/unfoldedcircle/integration-androidtv/issues/52)).

## v0.6.0 - 2024-06-14
### Added
- Allow manual URL / app-id option for app launch ([#47](https://github.com/unfoldedcircle/integration-androidtv/issues/47)).
- Quickline TV app. Added by @splattner, thanks! 
### Changed
- Update androidtvremote2 library to 0.1.1 ([#51](https://github.com/unfoldedcircle/integration-androidtv/pull/51)).

## v0.5.1 - 2024-05-14
### Added
- Profiles for TPV (Philips) TV and Dune HD Homatics media player. Contributed by @Kat-CeDe, thanks! ([#43](https://github.com/unfoldedcircle/integration-androidtv/pull/43))
- Apps 1und1 TV (Germany) and Arte.
- Special apps for TPV (Philips TV):
  - DVB-C/T/S switch to internal tuner
  - ATV Inputs opens a menu to change channels and inputs
  - ATV PlayFI open PlayFI Settings (Philips only)
  - ATV Now on TV opens an overview what is show on internal tuner
  - ATV Media opens a menu to browse connected media and play them
  - ATV Browser opens the web browser
### Fixed
- Update androidtvremote2 library to fix connection error. Discovered and fixed by @albaintor, thanks! ([#40](https://github.com/unfoldedcircle/integration-androidtv/issues/40)).

## v0.5.0 - 2024-04-01
### Added
- Support multiple Android TV instances and French translation. Contributed by @albaintor, thanks! ([#14](https://github.com/unfoldedcircle/integration-androidtv/issues/14)).
- New media-player features and device profiles (generic, Google Chromecast, NVIDIA Shield TV) ([#34](https://github.com/unfoldedcircle/integration-androidtv/pull/34)).
- Additional Google Android TV app mappings.
- Kodi app mapping and German setup translation. Contributed by @Kat-CeDe, thanks!
### Fixed
- Same Android TV device cannot be controlled by multiple Remote Two devices ([#39](https://github.com/unfoldedcircle/integration-androidtv/issues/39)).
- Improved reconnection handling ([#28](https://github.com/unfoldedcircle/integration-androidtv/issues/28)).
- Re-authenticate a configured device with an invalid certificate ([#36](https://github.com/unfoldedcircle/integration-androidtv/pull/36)).
- Power on/off command handling based on device state.
### Changed
- Enable logging for the androidtvremote2 module ([#28](https://github.com/unfoldedcircle/integration-androidtv/issues/28)).

## v0.4.6 - 2024-02-17
### Fixed
- Remove reconnect delay after standby. Requires new Remote Two firmware ([unfoldedcircle/feature-and-bug-tracker#320](https://github.com/unfoldedcircle/feature-and-bug-tracker/issues/320)).

## v0.4.5 - 2024-01-09
### Added
- More app mappings

## v0.4.4 - 2023-11-18
### Added
- Manual setup mode

## v0.4.3 - 2023-11-15
### Fixed
- Remove configured device if removed from configuration
- Set correct version in driver.json

## v0.4.2 - 2023-11-15
### Fixed
- Discovery exception during reconnect

## v0.4.1 - 2023-11-08
### Changed
- Use Python 3.11 and update dependencies

## v0.4.0 - 2023-11-07
### Added
- media-player features: menu, color_buttons, ff, rewind
- show device IP in setup discovery

### Fixed
- handle abort driver setup event
- setup flow retry handling

### Changed
- Use integration library 0.1.1 from PyPI
- Connection and state handling code refactoring, simplify media-player command handling
