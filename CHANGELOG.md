# Android TV integration for Remote Two Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## Unreleased

_Changes in the next release_

---

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
