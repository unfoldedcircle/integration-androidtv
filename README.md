# Android TV integration for Remote Two

Using [androidtvremote2](https://github.com/tronikos/androidtvremote2) and [uc-integration-api](https://github.com/aitatoi/integration-python-library)


## Build self-contained binary

After some tests, turns out python stuff on embedded is a nightmare. So we're better off creating a single binary file that has everything in it.

To do that, we need to compile it on the target architecture as `pyinstaller` does not support cross compilation.

The following can be used on x86 Linux:

```
sudo apt-get install qemu binfmt-support qemu-user-static
docker run --rm --privileged multiarch/qemu-user-static --reset -p yes
docker run --platform=aarch64 -v "$PWD:/io" -it ubuntu:focal

cd /io
apt-get update && apt-get install -y python3-pip
pip3 install pyinstaller -r requirements.txt
pyinstaller --clean --onefile driver.py
