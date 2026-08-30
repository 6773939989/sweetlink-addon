# Security Policy

## Supported versions

Only the current release of the add-on is supported. Hubs update themselves from this repository,
so "current" means the version in [`addon/config.yaml`](addon/config.yaml). Older versions receive
no fixes.

## Reporting a vulnerability

Write to <https://sweetplace.me/support>, describing what you found
and how to reproduce it. Please do not open a public issue for something that is exploitable.

We will confirm receipt and tell you what we intend to do about it. If a report leads to a change,
the change is recorded in [`addon/CHANGELOG.md`](addon/CHANGELOG.md).

There is **no bug bounty programme**. We would rather say so than leave a promise nobody has
decided to keep.

## Scope

This repository contains the add-on that runs on a Sweetplace hub. The backend it talks to is not
published here; reports about it are welcome at the same address.

Note that the add-on is a fork of somebody else's work (see [`NOTICE.md`](NOTICE.md)). A problem in
code inherited from upstream and still reachable here is in scope for us, and worth reporting to
them as well.
