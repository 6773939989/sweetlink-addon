<h1 align="left" style="margin-bottom:20px">Sweetlink (Homeway.io Fork)</h1>

> **Note:** the public repository [`6773939989/sweetlink-addon`](https://github.com/6773939989/sweetlink-addon)
> is generated automatically by the release process and exists only so that Home Assistant can
> install and update the add-on. It does not accept contributions: any change made there is
> overwritten at the next release.

Sweetlink is a custom fork of the official [Homeway.io](https://homeway.io) Home Assistant Add-on, tailored specifically for the Sweetplace ecosystem. 

It retains all the core secure tunneling and cloud synchronization features of Homeway (enabling official Alexa and Google Assistant integrations) while introducing private enterprise features for secure hardware deployments.

## 🌟 Custom Sweetplace Features

This fork introduces the following capabilities on top of the Homeway core:

- **Zero-Touch Provisioning (Hardware Claiming):** At startup, the AddOn discovers the hardware's physical MAC addresses and reports them to the Sweetplace backend together with its locally generated plugin id and private key. This allows end-users to link their physical device with zero technical configuration. The report runs independently of the Homeway connection: it sends only locally-known data, so hub registration no longer waits on homeway.io. Note that the public hostname itself is assigned by the Sweetplace backend, not by the device — the add-on deliberately submits an empty URL so the backend can mint or preserve it.
- **Granular Entity Filtering (YAML):** Bypasses the standard Home Assistant UI toggle system. The AddOn strictly enforces exposure rules based on local YAML configuration files (`alexa.yaml`, `google_assistant.yaml`), ensuring only whitelisted entities ever leave the local network.

## 🤝 Upstream Features (Homeway.io)

This project is proudly built on the shoulders of the [Homeway.io](https://homeway.io) open-source project. It inherits:
- Free remote access to Home Assistant
- Native Alexa and Google Assistant cloud integrations
- Fast WebRTC camera streaming

*Note: For official Homeway support, please visit the official Homeway community. This custom fork is maintained privately for the Sweetplace system and is not supported by the original Homeway developers.*

## 📜 License
This project is licensed under the AGPLv3 License, in compliance with the original upstream Homeway repository.
