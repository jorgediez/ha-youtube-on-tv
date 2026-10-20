# Security policy

## Reporting a vulnerability

Please report it privately, through
[Report a vulnerability](https://github.com/jorgediez/ha-youtube-on-tv/security/advisories/new)
on this repository's Security tab. Don't open a public issue or post it in the
community thread. Only the maintainer sees the report, and no email address is
needed.

Describe what you found, how to reproduce it, and which versions of the
integration and Home Assistant you saw it on.

## What counts

- Anything that leaks the TV's screen id or the Lounge session token, for
  example through diagnostics, entity attributes or the integration's own logs.
  Either one gives control of the TV's YouTube app.
- Diagnostics that contain what they are meant to leave out: the TV's address,
  or what was being watched.
- Anything that lets someone control the TV through Home Assistant beyond what
  the integration's entities and actions offer.
- A way to make the integration run code, or crash Home Assistant, from what a
  TV or YouTube's servers send.

Not a vulnerability in this integration:

- **DIAL has no authentication.** A TV that publishes YouTube over DIAL gives
  its screen id to anything on the same network that asks. That's how these TVs
  work, and nothing the integration does can change it. Keep the TV on a network
  you trust.
- **`pyytlounge: debug` logs the session token.** That's the library's own debug
  logging, and [Troubleshooting](docs/troubleshooting.md) says to leave it out of
  shared logs.

## Supported versions

Only the latest release gets fixes.

## What to expect

This is a volunteer project. Reports are read and fixed on a best-effort basis,
and you'll hear back in the report itself, where any fix and its release are
coordinated before details are made public.
