[![CI](https://github.com/ToxMCP/admetlab-mcp/actions/workflows/ci.yml/badge.svg)](https://github.com/ToxMCP/admetlab-mcp/actions/workflows/ci.yml)
[![DOI](https://img.shields.io/badge/DOI-10.64898%2F2026.02.06.703989-blue)](https://doi.org/10.64898/2026.02.06.703989)
[![License](https://img.shields.io/badge/License-Apache--2.0-blue.svg)](./LICENSE)
[![Release](https://img.shields.io/github/v/release/ToxMCP/admetlab-mcp?sort=semver)](https://github.com/ToxMCP/admetlab-mcp/releases)
[![Python](https://img.shields.io/badge/Python-3.9%2B-3776AB?logo=python&logoColor=white)](https://www.python.org/)

# ADMETlab MCP (ADMETlab 3.0 MCP Server)

> Part of **ToxMCP** Suite → https://github.com/ToxMCP/toxmcp


**Public MCP endpoint for the ADMETlab 3.0 API.**  
Expose molecule washing, SVG rendering, ADMET prediction, and CSV retrieval to any MCP-aware agent (Codex CLI, Gemini CLI, Claude Code, etc.).

## Why this project exists

ADMETlab 3.0 provides ADMET property calculations, washing, and visualization. Researchers often script against the API or copy/paste results; MCP packaging makes these workflows discoverable and callable by LLM copilots with guardrails and structured schemas.

---

## Feature snapshot

| Capability | Description |
| --- | --- |
| 🌐 **MCP over HTTP** | JSON-RPC `/mcp` endpoint with lifecycle + tool catalog. |
| 🧪 **ADMET Tools** | Wash molecules, render SVGs, run ADMET predictions, fetch CSV outputs. |
| 🛡️ **Client-side Guardrails** | Rate limit (<=5 rps), batching (<=1000 SMILES per call), retries/backoff, fallback endpoints for ADMET. |
| 📜 **Schema-first** | Pydantic models + JSON Schema surfaced via `tools/list`. |
| 🔎 **Observability Ready** | Structured JSON logs with correlation IDs; hooks for metrics/audit. |

---

## Quick start

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
cp .env.example .env
uvicorn admetlab_mcp.transport.http:app --host 0.0.0.0 --port 8200 --reload
```

MCP HTTP endpoint: `http://localhost:8200/mcp`  
Health: `http://localhost:8200/health`

> Note: ADMET predictions try `/api/admet` then fall back to `/api/single/admet`. The official site currently reports instability; expect occasional 5xx/404 responses from upstream.

---

## Configuration

Settings use `pydantic-settings` with `.env` support (prefix `ADMETLAB_`):

| Variable | Default | Description |
| --- | --- | --- |
| `ADMETLAB_BASE_URL` | `https://admetlab3.scbdd.com` | Base URL for all requests. |
| `ADMETLAB_TIMEOUT_SECONDS` | `30` | HTTP timeout. |
| `ADMETLAB_RETRY_ATTEMPTS` | `3` | Retry attempts on 5xx/429. |
| `ADMETLAB_RETRY_BACKOFF` | `0.5` | Initial backoff seconds (exponential). |
| `ADMETLAB_RPS_LIMIT` | `5` | Client-side requests per second cap. |
| `ADMETLAB_BATCH_SIZE` | `1000` | SMILES per request before chunking. |
| `ADMETLAB_FEATURE_DEFAULT` | `false` | Default `feature` flag for ADMET. |
| `ADMETLAB_UNCERTAIN_DEFAULT` | `false` | Default `uncertain` flag for ADMET. |
| `ADMETLAB_ADMET_ENDPOINT` | `/api/admet` | Primary ADMET endpoint. |
| `ADMETLAB_ADMET_FALLBACK_ENDPOINTS` | `/api/single/admet` | Fallback endpoints. Accepts comma-separated paths or JSON array string. |
| `ADMETLAB_API_KEY` | _empty_ | Reserved for future auth. |
| `ADMETLAB_LOG_LEVEL` | `INFO` | Log level. |

---

## Tool catalog

| Tool | Upstream | Description |
| --- | --- | --- |
| `wash_molecule` | `POST /api/washmol` | Standardize molecules; returns cleaned SMILES list. |
| `render_molecule_svg` | `POST /api/molsvg` | Render molecule SVG; optional `figsize` `[w,h]`. |
| `predict_admet` | `POST /api/admet` (fallback `/api/single/admet`) | ADMET panel with decision codes, probabilities, SVG highlights; returns batch aggregation and `taskid` from upstream payload. |
| `fetch_admet_csv` | `POST /api/admetCSV` | Fetch CSV results by `taskId`; response includes headers and content. |

Lifecycle: `initialize`, `initialized`, `shutdown`, `exit` exposed via `/mcp`. Tool schemas are discoverable via `tools/list`.

---

## Running the server

```bash
uvicorn admetlab_mcp.transport.http:app --host 0.0.0.0 --port 8200
```

Sample MCP calls (HTTP):

```bash
# initialize
curl -s http://localhost:8200/mcp \
  -H "Content-Type: application/json" \
  -d '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{}}'

# list tools
curl -s http://localhost:8200/mcp \
  -H "Content-Type: application/json" \
  -d '{"jsonrpc":"2.0","id":2,"method":"tools/list","params":{}}'

# wash molecule
curl -s http://localhost:8200/mcp \
  -H "Content-Type: application/json" \
  -d '{"jsonrpc":"2.0","id":3,"method":"tools/call","params":{"name":"wash_molecule","arguments":{"SMILES":"CCO"}}}'
```

---

## Output artifacts

- Tool results are returned as JSON under `result.content` in MCP JSON-RPC responses.
- CSV fetch includes raw text plus headers for client-side saving.
- SVGs are returned inline as strings from `render_molecule_svg`.

---

## Security checklist

- Client-side rate limiting honors service guidance (`<=5 rps`).
- Input validation and batch caps to avoid oversize requests.
- Optional API key header placeholder for future auth.
- Prefer running behind TLS-terminating proxy; restrict exposure to trusted clients.

---

## Development notes

- Tests: `pytest`
- Lint/format: `black . && isort .`
- Known upstream issues: ADMET endpoints may return 404/500 due to service instability (per official notice). The client retries and falls back but cannot guarantee success.

---

## Contributing

See [`CONTRIBUTING.md`](CONTRIBUTING.md) for development setup and pull request guidance.

## Community and governance

- Code of Conduct: [`CODE_OF_CONDUCT.md`](CODE_OF_CONDUCT.md)
- Security policy: [`SECURITY.md`](SECURITY.md)
- Release checklist: [`RELEASE_CHECKLIST.md`](RELEASE_CHECKLIST.md)
- Changelog: [`CHANGELOG.md`](CHANGELOG.md)

## License

MIT. See [`LICENSE`](LICENSE).
## Acknowledgements / Origins

This work was developed in the context of the **VHP4Safety** project and related efforts. It builds on upstream third-party data/services (see repository documentation for exact dependencies and access requirements).
