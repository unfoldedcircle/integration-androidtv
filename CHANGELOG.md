# Android TV integration for Remote Two Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## Unreleased

_Changes in the next release_

---
## v0.5.0 - 2024-04-21
### Added
- Profiles for TPV (Philips) TV and Dune HD Homatics media player
- apps 1und1 TV (Germany) and Arte
- special apps for TPV (Philips TV)
    DVB-C/T/S switch to internal tuner
    ATV Inputs opens a menu to change channels and inputs
    ATV PlayFI open PlayFI Settings (Philips only)
    ATV Now on TV opens an overview what is show on internal tuner
    ATV Media opens a menu to browse connected media and play them
    ATV Bopens the web browser

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
