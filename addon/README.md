# Sweetlink

The Home Assistant add-on that turns a Sweetplace hub into a device its owner can set up, reach and
share from a phone, without touching a configuration file.

Sweetlink is not a general-purpose add-on and is not meant to be installed by hand: it ships
pre-installed on Sweetplace hubs and is configured during activation.

## What it does

- **Registers the hub.** At startup it reports the board's physical network addresses together with
  an identity generated on the device — an id and a private key that only ever leave it to
  authenticate it.
- **Puts the hub on the internet.** Each hub gets its own Cloudflare tunnel and its own hostname. No
  ports opened on the router, no fixed IP, no per-device subscription.
- **Hands the hub to its owner.** A code printed on the label under the device, next to a QR the
  panel draws. The customer scans it, confirms an email address, and from that moment the hub is
  theirs.
- **Creates the household accounts.** The owner and everyone they add get a Home Assistant account
  and a password generated once. They are all standard users, never administrators.
- **Sets the home position.** The address confirmed during activation becomes the coordinates of the
  Home zone.
- **Limits login attempts.** Five wrong passwords and the address is locked out; the owner can lift
  the block for their own connection, and only for that one.
- **Prepares the device for cloning.** Before a disk is duplicated, the panel reports what is still
  on it that must not be copied — identities, keys, accounts.

## Setup

Nothing to do here. The hub is delivered activated; the panel in the sidebar shows its public
address, its state, and the way back into the household settings.

## Support

Sweetplace hubs are supported through [sweetplace.me/support](https://sweetplace.me/support).

## Origin, and what this is derived from

Sweetlink is a fork of the [Homeway.io Home Assistant add-on](https://github.com/homewayio/AddOn),
which is itself derived from
[OctoEverywhere](https://github.com/QuinnDamerell/OctoPrint-OctoEverywhere). Both are licensed
under the GNU Affero General Public License; so is this fork. See
[`LICENSE`](https://github.com/6773939989/sweetlink-addon/blob/main/LICENSE) and
[`NOTICE.md`](https://github.com/6773939989/sweetlink-addon/blob/main/NOTICE.md), which records what
was changed and when.

**This fork is not supported by, affiliated with, or endorsed by Homeway.io or OctoEverywhere, and
they cannot help with it.** For their projects, use their own channels — not ours, and not this
repository.
