# Requirements and Settings

## Requirements

- Only devices running the [Android TV Remote Service](https://play.google.com/store/apps/details?id=com.google.android.tv.remote.service)
  are supported.
  - ‼️ This service does not work on Fire TV devices.
- Only devices in the same network can be discovered.
- The mDNS protocol must be allowed in your network.
  - In most consumer networks the mDNS protocol is allowed and when the Remote and the Android TV device(s) are on the
  same network, the Remote should have no difficulties finding the device(s).
  - When using mesh networking or professional networking gear, mDNS may be disabled or only specific mDNS services may
    be allowed.  
    In such cases, please check the manufacturer's documentation on how to enable mDNS or specific mDNS services.
- When using DHCP: a static IP address reservation for the Android TV devices is recommended.  
  A fixed IP address can speed up reconnection after the Remote wakes up from standby.

## Limitations and Known Issues

- During the setup process, you have to enter a PIN code shown on your Android TV.
  - Please make sure that your Android TV is powered on and that no old pairing request is shown.
  - If pairing continuously fails, reboot your Android TV device and try again.
  - If sending commands doesn't work after pairing or the integration is repeatedly disconnected, try rebooting the
    Android TV device.
- Verify with the Google TV mobile app or the Google Home mobile app to send commands to the Android TV device.
  - If these don't work, neither will this integration.
- Not every app will work or supports all keycodes.
  - For example, some IP-TV apps don't support channel-up & down commands.  
- Retrieving the installed applications is not supported.
  - The shown apps in the input selection list are a pre-defined list of common applications.
- Some devices, like TCL, become unavailable after they are turned off, unless you activate the `Screenless service`.  
  - Activate it under: Settings, System, Power and energy: Screenless service
- Voice commands are a recently reverse-engineered feature and may not work on all devices.
  - Streaming voice while speaking is currently disabled, since the detection becomes much more unreliable.
  - The full voice command is buffered in the integration and sent after the voice stream stops (when releasing the
    microphone button.

See also the known issues of [Home Assistant's Android TV Remote Integration](https://www.home-assistant.io/integrations/androidtv_remote/#limitations-and-known-issues),
since it is using the same communication library.

## Application Settings

### Kodi

If navigation doesn't work within the Kodi app, an additional input controller needs to be configured first with the
original Android TV remote:

1. Start Kodi
2. Go to Settings, System, Input: `Configure attached controllers`
3. Select `Get more...`
4. Scroll down and select `TV Remote`
5. Configure buttons: select a button in the list with the original remote, then confirm it with the corresponding
   Remote Two button.
