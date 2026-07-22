# Release Checklist

Use this checklist before creating a new release.

## 1. Scope and versioning

- Confirm release scope and target version (`major.minor.patch`).
- Ensure `src/admetlab_mcp/__init__.py` and `pyproject.toml` version values match.
- Confirm changelog/release notes entries are drafted.

## 2. Quality gates

- Run tests: `pytest`
- Run formatting checks: `black .` and `isort .`
- Run a local smoke test:
  - `GET /health`
  - `GET /readyz`
  - `POST /mcp` `initialize`
  - `POST /mcp` `tools/list`

## 3. Documentation and metadata

- Confirm `README.md` reflects current tool behavior.
- Confirm `.env.example` includes all required variables and no unrelated keys.
- Confirm `SECURITY.md`, `CODE_OF_CONDUCT.md`, and `CONTRIBUTING.md` are current.

## 4. Git and tagging

- Merge release changes into `main`.
- Create an annotated git tag (`vX.Y.Z`).
- Push commit and tag to origin.

## 5. GitHub release

- Create a GitHub release from the tag.
- Include:
  - summary of major changes,
  - migration notes (if any),
  - known limitations/upstream caveats.

## 6. Post-release verification

- Verify CI succeeded on tag/release commit.
- Re-run a quick MCP smoke test from a clean environment.
- Announce release and link to notes.
