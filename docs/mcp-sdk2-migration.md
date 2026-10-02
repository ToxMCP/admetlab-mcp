# SDK2 migration candidate

0.2.0 adopts stable Python MCP SDK 2.2.0 and protocol 2026-07-28. The published version remains 0.1.3. Python 3.10 remains supported. Uvicorn's old `<0.31` cap conflicted with SDK2's minimum `0.31.1`; the candidate updates the server dependency and verifies the supported Python matrix.

Existing clients keep `/mcp` and the same custom legacy handler, released `2024-11-05` initialization revision, notification responses and `tools.list` / `tools.call` aliases. Only requests claiming a modern protocol through their header or namespaced request metadata enter the SDK2 handler. Modern responses use stateless JSON and private catalog cache hints. The four names, schemas and core annotations are unchanged. The legacy `predict_admet.metadata` batch/rate settings appear under modern `tool._meta["org.toxmcp/toolMetadata"]`.

Upstream data, model interpretation and client request contracts are unchanged. Both handlers use the same application-lifespan upstream client and process-wide request-start/retry budget. Prediction remains one provider request per SMILES, with deprecated `uncertain` ignored, explicit source labels and honest `isError` outage/rejection results. SDK2 cannot repair an external provider outage.

## Hosting

Set `ADMETLAB_ALLOWED_HOSTS` to the public authority (comma separated), and `ADMETLAB_ALLOWED_ORIGINS` to allowed browser origins. Empty values default to loopback authorities/origins with any port. Modern SDK HTTP validates these independently. This server retains its existing authentication model: inbound authentication is the gateway's responsibility; `ADMETLAB_API_KEY` is an upstream provider header.

Both handlers now reject bodies above `ADMETLAB_MAX_REQUEST_BYTES` (default 1,000,000 bytes), including chunked bodies without Content-Length. Increase this explicit bound for an existing deployment that needs larger payloads. Settings and default batch/rate limits remain independent.

## Local stdio

Use `admetlab-mcp-stdio` or `python -m admetlab_mcp.transport.sdk2`. Both SDK generations negotiate it. The new local transport writes logs to stderr so stdout remains exclusively JSON-RPC. HTTP keeps its existing logging default.

## Compatibility checks

The offline real-client matrix exercises isolated MCP 1.30.0 and 2.2.0 clients over HTTP and stdio. Eight workflows cover washing, rendering, single/batch predictions, deprecated input, CSV retrieval and both outage/rejection errors. Every combination sends the same nine upstream HTTP requests and returns the same application results. The synthetic provider is a test-only HTTPX MockTransport below the real production upstream client; production entry points never import it. No synthetic value claims real scientific model validity.

The comparison validates and excludes only additive SDK2 completion/server-identity envelopes and an explicit false error flag; true domain error flags, text, structured data and source labels remain compared. The pre-version candidate is also compared with the clean released v0.1.3 source and its original frozen runtime.

```bash
uv sync --locked --extra dev
uv sync --locked --project tests/compatibility/legacy-client
uv run --no-sync python scripts/verify_protocol_clients.py \
  --legacy-python "$PWD/tests/compatibility/legacy-client/.venv/bin/python" \
  --server-root "$PWD/src" --output-dir /tmp/admetlab-client-results
```

Installed-wheel CI runs both SDKs from outside the checkout. Unit tests verify modern protocol/header/authority checks, streamed body rejection, all four legacy revision headers, upstream input validation, provider failures and concurrent calls sharing the real rate budget.

[Official SDK2 migration guide](https://py.sdk.modelcontextprotocol.io/migration/)
