# Contributing

## Development setup

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
cp .env.example .env
```

## Local checks

```bash
pytest
black .
isort .
```

## Pull requests

- Keep changes scoped and include tests for behavior changes.
- Update documentation when APIs/configuration change.
- Ensure `pytest` passes before opening a PR.

## Reporting issues

Use [GitHub Issues](https://github.com/senseibelbi/ADMETlab_MCP/issues) with:
- clear reproduction steps,
- expected vs actual behavior,
- logs or payloads when relevant.
