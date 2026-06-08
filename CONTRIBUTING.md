# Contributing to mcp-tmux

Thanks for your interest in improving **mcp-tmux**! Contributions of all kinds
are welcome — bug reports, feature requests, docs, and code.

## Where things live

- Source: [`github.com/laszlopere/mcp-tmux`](https://github.com/laszlopere/mcp-tmux)
- Issues & feature requests: <https://github.com/laszlopere/mcp-tmux/issues>
- Package source lives under `src/mcp_tmux/`; tests under `tests/`.

## Development setup

```bash
python -m venv .venv && . .venv/bin/activate
pip install -e ".[dev]"
```

Or with [uv](https://docs.astral.sh/uv/):

```bash
uv pip install -e ".[dev]"
```

## Checks before opening a PR

The CI runs these on Python 3.10–3.13; please run them locally first:

```bash
ruff check .            # lint
ruff format --check .   # formatting
mypy                    # type-check (src/)
pytest                  # unit tests always run; integration tests need tmux
```

The integration tests drive a real tmux server and are skipped automatically if
`tmux` isn't on `PATH`. Install tmux (`apt install tmux`, `brew install tmux`, …)
to run the full suite.

## Guidelines

- **Universality matters.** The server targets tmux **1.8+** (≈2013). Gate any
  newer flags/format variables behind a version check (see `capabilities.py`)
  rather than assuming a modern tmux.
- Keep curated tools ergonomic; anything exotic can go through the
  `tmux_command` passthrough instead of growing the surface area.
- Match the surrounding code style — type hints, no stray `type: ignore`.
- Update the README's tool table and docs when you add or change a tool.

## Reporting bugs

Use the issue templates. Please include your mcp-tmux version, Python version,
the tmux version on the server host (`tmux -V`), and the target (local /
`user@host` / named profile).

## License

By contributing, you agree that your contributions are licensed under the
project's [MIT License](LICENSE).
