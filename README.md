# Android TV integration for Remote Two

Using [androidtvremote2](https://github.com/tronikos/androidtvremote2) and [uc-integration-api](https://github.com/aitatoi/integration-python-library).

The integration currently supports almost all features that the androidtvremote2 library provides.
Button control and ON/OFF states are supported. Unfortunately media image and playing information are not retrievable.
Source list is limited to a predefined list as retrieving a list of installed apps is not possible.

This integration is included in the Remote Two firmware and no external service must be run to connect with Android TV
devices. A standalone service can be used for development or connecting multiple devices.
A single instance of the integration doesn't support multiple Android TV devices yet (planned: [#14](https://github.com/aitatoi/integration-androidtv/issues/14)).

Device profiles allow device specific support and custom key bindings, for example double-click or long-press actions.
See [command mappings](docs/command_mapping.md) for more information.

## Standalone Usage
### Setup

- Requires Python 3.11
- Install required libraries:  
  (using a [virtual environment](https://docs.python.org/3/library/venv.html) is highly recommended)
```shell
pip3 install -r requirements.txt
```

For running a separate integration driver on your network for Remote Two, the configuration in file
[driver.json](driver.json) needs to be changed:

- Set `driver_id` to a unique value, `uc_androidtv_driver` is already used for the embedded driver in the firmware.
- Change `name` to easily identify the driver for discovery & setup  with Remote Two or the web-configurator.
- Optionally add a `"port": 8090` field for the WebSocket server listening port.
  - Default port: `9090`
  - Also overrideable with environment variable `UC_INTEGRATION_HTTP_PORT`

### Run

```shell
python3 intg-androidtv/driver.py
```

See available [environment variables](https://github.com/unfoldedcircle/integration-python-library#environment-variables)
in the Python integration library to control certain runtime features like listening interface and configuration directory.

## Build self-contained binary

After some tests, turns out python stuff on embedded is a nightmare. So we're better off creating a single binary file
that has everything in it.

To do that, we need to compile it on the target architecture as `pyinstaller` does not support cross compilation.

### x86-64 Linux

On x86-64 Linux we need Qemu to emulate the aarch64 target platform:
```bash
sudo apt install qemu binfmt-support qemu-user-static
docker run --rm --privileged multiarch/qemu-user-static --reset -p yes
```

Run pyinstaller:
```shell
docker run --rm --name builder \
    --platform=aarch64 \
    --user=$(id -u):$(id -g) \
    -v "$PWD":/workspace \
    docker.io/unfoldedcircle/r2-pyinstaller:3.11.6  \
    bash -c \
      "python -m pip install -r requirements.txt && \
      pyinstaller --clean --onefile --name intg-androidtv intg-androidtv/driver.py"
```

### aarch64 Linux / Mac

On an aarch64 host platform, the build image can be run directly (and much faster):
```shell
docker run --rm --name builder \
    --user=$(id -u):$(id -g) \
    -v "$PWD":/workspace \
    docker.io/unfoldedcircle/r2-pyinstaller:3.11.6  \
    bash -c \
      "python -m pip install -r requirements.txt && \
      pyinstaller --clean --onefile --name intg-androidtv intg-androidtv/driver.py"
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
