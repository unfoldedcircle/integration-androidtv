# Android TV integration for Remote Two/3

Using [androidtvremote2](https://github.com/tronikos/androidtvremote2), [uc-integration-api](https://github.com/aitatoi/integration-python-library),
[pychromecast](https://github.com/home-assistant-libs/pychromecast) and [google-play-scraper](https://github.com/JoMingyu/google-play-scraper).

The integration currently supports almost all features that the `androidtvremote2` library provides.
Button control and ON/OFF states are supported. With the optional Google Cast support, media playing information can be 
retrieved from supported apps.  
The application source list is limited to a predefined list, as retrieving the installed apps is not possible.

This integration is included in the Remote Two and Remote 3 firmware, and no external service must be run to connect
with Android TV devices.

‼️ Do not install this integration as a custom integration on the Remote, or it can interfere with the included version.  
Included integrations cannot be updated manually. The integration can be run as an external integration for testing and 
development.


- [Requirements and setting](docs/settings.md).
- Multiple Android TV devices are supported with version 0.5.0 and newer.
- A [media player entity](https://github.com/unfoldedcircle/core-api/blob/main/doc/entities/entity_media_player.md)
  is exposed per Android TV device to the Remote.
- Device profiles allow device-specific support and custom key bindings, for example, double-click or long-press actions.  
  See [command mappings](docs/command_mapping.md) for more information.

Preview features:
- Optional external metadata lookup using the Google Play Store for friendly application name and icon.
- Google Cast support to retrieve media-playing information.
- Google Cast volume control with configurable volume step.

The preview features are not enabled by default. They can be enabled in the device configuration of the setup flow.

## Standalone Usage
### Setup

- Requires Python 3.11
- Install required libraries:  
  (using a [virtual environment](https://docs.python.org/3/library/venv.html) is highly recommended)
```shell
pip3 install -r requirements.txt
```

For running a separate integration driver on your network for Remote Two/3, the configuration in file
[driver.json](driver.json) needs to be changed:

- Set `driver_id` to a unique value, `uc_androidtv_driver` is already used for the embedded driver in the firmware.
- Change `name` to easily identify the driver for discovery & setup  with Remote Two/3 or the web-configurator.
- Optionally add a `"port": 8090` field for the WebSocket server listening port.
  - Default port: `9090`
  - Also overrideable with environment variable `UC_INTEGRATION_HTTP_PORT`

### Run

```shell
UC_CONFIG_HOME=./config UC_DATA_HOME=./data python3 src/driver.py
```

- Environment variables:
  - `UC_CONFIG_HOME`: configuration directory for device settings, certificates and profiles.
  - `UC_DATA_HOME`: data directory to store metadata images.
- See available [environment variables](https://github.com/unfoldedcircle/integration-python-library#environment-variables)
  in the Python integration library to control certain runtime features like listening interface and configuration directory.
- The client name used for the client certificate can be set in ENV variable `UC_CLIENT_NAME`.
  The hostname is used by default. 

## Build distribution binary

After some tests, turns out Python stuff on embedded is a nightmare. So we're better off creating a binary distribution
that has everything in it, including the Python runtime and all required modules and native libraries.

To do that, we use [PyInstaller](https://pyinstaller.org/), but it needs to run on the target architecture as
`PyInstaller` does not support cross compilation.

The `--onefile` option to create a one-file bundled executable should be avoided:
- Higher startup cost, since the wrapper binary must first extract the archive.
- Files are extracted to the /tmp directory on the device, which is an in-memory filesystem.  
  This will further reduce the available memory for the integration drivers! 

### x86-64 Linux

On x86-64 Linux we need Qemu to emulate the aarch64 target platform:
```bash
sudo apt install qemu-system-arm binfmt-support qemu-user-static
docker run --rm --privileged multiarch/qemu-user-static --reset -p yes
```

Run PyInstaller:
```shell
docker run --rm --name builder \
    --platform=aarch64 \
    --user=$(id -u):$(id -g) \
    -v "$PWD":/workspace \
    docker.io/unfoldedcircle/r2-pyinstaller:3.11.12  \
    bash -c \
      "python -m pip install -r requirements.txt && \
      pyinstaller --clean --onedir --name intg-androidtv src/driver.py"
```

### aarch64 Linux / Mac

On an aarch64 host platform, the build image can be run directly (and much faster):
```shell
docker run --rm --name builder \
    --user=$(id -u):$(id -g) \
    -v "$PWD":/workspace \
    docker.io/unfoldedcircle/r2-pyinstaller:3.11.12  \
    bash -c \
      "python -m pip install -r requirements.txt && \
      pyinstaller --clean --onedir --name intg-androidtv src/driver.py"
```

## Versioning

We use [SemVer](http://semver.org/) for versioning. For the versions available, see the
[tags and releases in this repository](https://github.com/unfoldedcircle/integration-androidtv/releases).

## Changelog

The major changes found in each new release are listed in the [changelog](CHANGELOG.md)
and under the GitHub [releases](https://github.com/unfoldedcircle/integration-androidtv/releases).

## Contributions

Please read our [contribution guidelines](CONTRIBUTING.md) before opening a pull request.

## License

This project is licensed under the [**Mozilla Public License 2.0**](https://choosealicense.com/licenses/mpl-2.0/).
See the [LICENSE](LICENSE) file for details.
