<h1 align="left" style="margin-bottom:20px">Sweetlink</h1>

> **Note:** the public repository [`6773939989/sweetlink-addon`](https://github.com/6773939989/sweetlink-addon)
> is generated automatically by the release process and exists only so that Home Assistant can
> install and update the add-on. It does not accept contributions: any change made there is
> overwritten at the next release.

Sweetlink is the Home Assistant add-on that turns a Sweetplace hub into a device the owner can set
up, reach and share from their phone, without touching a configuration file.

## What it does

**Registers the hub, and proves which one it is.** At startup the add-on reads the physical MAC
addresses of the board and reports them to the Sweetplace backend together with a locally
generated identity: a plugin id and a private key that never leave the device except to
authenticate it. The public hostname is minted by the backend, not chosen here.

**Puts the hub on the internet through its own tunnel.** The backend provisions a Cloudflare
tunnel and the DNS record that points at it; the add-on runs the connector. The public name is a
random 122-bit label minted once and kept on the device's row, so it is stable for the life of the
device and survives a tunnel being recreated. It is deliberately *not* derived from the hardware:
a MAC is known to anyone who has ever been on the same local network, and only 24 bits of it vary
within a vendor prefix.

**Hands the hub to its owner.** A code is printed on the label under the device, next to a QR the
add-on draws in its own panel. The customer scans it, confirms an email address, and from that
moment the hub is theirs — the add-on panel stops offering the claim and starts offering the way
back in.

**Creates the accounts of the household.** The owner and every person they add get a Home Assistant
account, a login name they choose, and a password generated once. They are all standard users, never
administrators. Each person receives a single-use invite link; nobody types a password into this
add-on.

**Sets the home position.** The address confirmed during activation becomes the coordinates of the
Home zone, so presence and anything that depends on being home works without further setup.

**Keeps the login from being brute-forced.** The add-on writes the `http:` section Home Assistant
needs to see the real address of whoever is connecting, and closes an address out after five wrong
attempts. When that locks the household out of their own house, the owner can lift the block for
their own connection from the portal, and only for that one.

**Prepares the device for cloning.** Before a disk is duplicated onto a production run, the panel
reports what is still on it that must not be copied — identities, keys, accounts — and stays red
while anything is left. Cloning a configured device is the fastest way to ship a fleet that shares
one identity.

## Origin, and what this is derived from

    OctoEverywhere            https://github.com/QuinnDamerell/OctoPrint-OctoEverywhere
      └── Homeway.io add-on   https://github.com/homewayio/AddOn
            └── Sweetlink     this repository

Both upstream projects are distributed under the **GNU Affero General Public License**, and so is
this fork. [`NOTICE.md`](NOTICE.md) records what was changed and when; [`LICENSE`](LICENSE) is the
full text.

Parts of the upstream codebase are still in the tree. Where they reached services that are not
ours, they are no longer reachable.

**This fork is not supported by, affiliated with, or endorsed by Homeway.io or OctoEverywhere.**
Please do not take questions about it to their communities: they did not write this and cannot help
with it.
