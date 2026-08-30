# Sweetlink

The add-on that makes a Sweetplace hub reachable, deliverable and manageable from a phone.

It ships pre-installed and pre-configured on Sweetplace hubs. There is no manual installation
procedure, and nothing here needs to be set up by hand.

## The panel

The **Sweetlink** entry in the sidebar opens the add-on's own page. What it shows depends on who is
looking:

- an **administrator** of this Home Assistant sees everything: the hub's public address and state,
  the household, the technical details, and the pre-cloning report;
- the **owner of the home** — a standard user, not an administrator — sees the household and the
  way into the portal;
- everyone else has no reason to open it and finds an explanation instead.

### Before the hub is handed over

The panel draws the QR code and the claim code that go on the label under the device. The customer
scans the QR, or types the code on the activation site. Without that code nobody can claim the hub.

### After it has been handed over

The panel shows the hub's public address, whether the tunnel is up, and a button that opens the
household settings with the device already identified — no second login.

### Technical details

Hardware address, public address and claim code: the three values support asks for when something
is not working.

### Preparing an image for cloning

Duplicating the disk of a configured hub is the fastest way to ship a fleet that shares one
identity. Before cloning, the panel reports what is still on the device that must not be copied —
identity, keys, tunnel credentials, Home Assistant accounts, paired phones — and stays red while
anything is left.

**Wiping is not reversible.** The device goes back to the state it left the factory in, and whoever
had claimed it has to activate it again.

## Support

Sweetplace hubs are supported through [sweetplace.me/support](https://sweetplace.me/support).

## Origin

Sweetlink is a fork of the [Homeway.io Home Assistant add-on](https://github.com/homewayio/AddOn),
itself derived from [OctoEverywhere](https://github.com/QuinnDamerell/OctoPrint-OctoEverywhere),
both under the GNU Affero General Public License. Details in
[`NOTICE.md`](https://github.com/6773939989/sweetlink-addon/blob/main/NOTICE.md).

This fork is not supported by, affiliated with, or endorsed by either project.
