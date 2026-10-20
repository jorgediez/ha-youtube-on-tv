# Contributing

Thanks for looking. Reports from TVs other than the ones already listed are the most useful thing anyone can send: the integration talks to an undocumented protocol, and nearly everything it knows came from someone running it on hardware I don't have.

## Reporting a device, or a problem

The [community thread](https://community.home-assistant.io/t/youtube-on-tv-see-and-control-what-the-youtube-app-on-your-tv-is-playing/1026146) is the easiest place for "it works on X" or "X behaves oddly". Bugs are better as an [issue](https://github.com/jorgediez/ha-youtube-on-tv/issues/new/choose), where there is a form for bugs and one for telling us how the integration does on your device.

What makes a report actionable:

- The integration's diagnostics (*Settings → Devices & services → YouTube on TV → ⋮ → Download diagnostics*). The screen id, the TV's address and what was being watched are left out.
- A debug log covering the moment it went wrong, with `custom_components.youtube_on_tv: debug` in your `logger` settings. See [troubleshooting](docs/troubleshooting.md).
- For a device that isn't discovered, the output of `scripts/dial_scan.py`. See [development](docs/development.md).

A security problem should not go in an issue or the thread: see [SECURITY.md](SECURITY.md) for how to report it privately.

## What belongs here

This integration shows what the YouTube app on a TV is playing, and controls it, over the Lounge protocol that the YouTube phone app uses when casting. It works without a Google account, an API key, or anything that would need your YouTube credentials.

Changes that fit:

- More of what the TV already reports or accepts over the protocol.
- Making an existing feature work on a device where it doesn't.
- Fixing how the integration reacts to what a TV sends.
- Documentation, especially device behavior.

Changes I'd rather not take:

- Anything needing YouTube account credentials, cookies or OAuth.
- Scraping youtube.com, or depending on a library that does.
- Browsing, searching or recommendations: this integration is about the TV, not the account.
- A bundled dashboard card, which would be a second project to maintain.

None of that is a judgement on the idea. A YouTube account integration is a reasonable thing to build; it just has a different shape, a different failure mode and a different maintenance burden, and it would be a surprise inside a TV integration. It can live as its own integration and use this one for playback.

**If a change is more than a small fix, open an issue first.** A short description of what you want and how you'd do it takes minutes and can save you days.

## Working on the code

```bash
python3.14 -m venv .venv
.venv/bin/pip install -r requirements_test.txt
.venv/bin/ruff check . && .venv/bin/ruff format --check .
.venv/bin/pytest --cov
```

The Home Assistant test harness only runs on Linux and macOS. On Windows, use WSL, or `scripts/test-docker.sh`, which runs the suite in Docker against the oldest supported Home Assistant and the newest, as CI does.

A pull request is expected to:

- Pass `ruff check`, `ruff format --check` and the test suite. CI runs these against the oldest supported Home Assistant and the newest, plus HACS and `hassfest`, and needs a maintainer to approve the first run on a fork.
- Come with tests. Protocol behavior is best covered by replaying what a TV actually sent; `tests/test_entities.py` has examples taken from real captures.
- Update the docs it affects, in [docs/](docs). The README stays an overview.
- Explain, in the commit message, why the change is the way it is — particularly anything that works around how a TV behaves.

Keep a pull request to one subject. Two unrelated improvements are two pull requests, and they'll both move faster.

## Style

Follow [Home Assistant's own conventions](https://developers.home-assistant.io/docs/development_guidelines) and the code already here: entity names and errors translated, no blocking calls in the event loop, comments that say why rather than what.

## License

Contributions are under [GPL-3.0](LICENSE), like the rest of the project.
