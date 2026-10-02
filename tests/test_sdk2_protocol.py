from __future__ import annotations

import hashlib
import json
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

from admetlab_mcp.client import admet_client as upstream
from admetlab_mcp.settings import get_settings
from admetlab_mcp.transport import http as legacy
from admetlab_mcp.transport.sdk2 import TOOL_METADATA_KEY

PROTOCOL = "2026-07-28"


def post(client, method, params=None, headers=None):
    params = {
        **(params or {}),
        "_meta": {
            "io.modelcontextprotocol/protocolVersion": PROTOCOL,
            "io.modelcontextprotocol/clientCapabilities": {},
        },
    }
    request_headers = {
        "Accept": "application/json, text/event-stream",
        "MCP-Protocol-Version": PROTOCOL,
        "Mcp-Method": method,
    }
    if method == "tools/call":
        request_headers["Mcp-Name"] = params["name"]
    request_headers.update(headers or {})
    return client.post(
        "/mcp",
        headers=request_headers,
        json={"jsonrpc": "2.0", "id": 7, "method": method, "params": params},
    )


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setenv("ADMETLAB_RETRY_ATTEMPTS", "0")
    monkeypatch.setenv("ADMETLAB_RETRY_BACKOFF", "0")
    monkeypatch.setenv("ADMETLAB_ALLOWED_HOSTS", "")
    monkeypatch.setenv("ADMETLAB_ALLOWED_ORIGINS", "")
    get_settings.cache_clear()
    monkeypatch.setattr(
        upstream, "_PROCESS_RATE_LIMITER", upstream.UpstreamRateLimiter()
    )
    requests = []
    created = []

    async def handler(request):
        body = json.loads(request.content)
        requests.append((request.url.path, body, time.monotonic()))
        if body.get("SMILES") == "fixture-unavailable":
            return httpx.Response(
                500, request=request, json={"error": "synthetic provider exception"}
            )
        response = (
            {"washed": body["SMILES"]}
            if request.url.path == "/api/washmol"
            else {"taskid": "fixture-task", "SMILES": body["SMILES"]}
        )
        return httpx.Response(200, request=request, json=response)

    def factory():
        value = upstream.AdmetClient(
            client=httpx.AsyncClient(
                base_url="https://admetlab3.scbdd.com",
                transport=httpx.MockTransport(handler),
            )
        )
        created.append(value)
        return value

    monkeypatch.setattr(legacy, "AdmetClient", factory)
    legacy._prediction_availability.update(
        status="unknown", lastChecked=None, upstreamStatus=None
    )
    try:
        with TestClient(legacy.app, base_url="http://localhost:8200") as value:
            yield value, requests, created
    finally:
        get_settings.cache_clear()


def test_modern_catalog_retains_all_tool_contracts(client):
    value, _, _ = client
    result = post(value, "tools/list").json()["result"]
    assert result["ttlMs"] == 60_000 and result["cacheScope"] == "private"
    assert result["resultType"] == "complete"
    normalized = []
    for tool in result["tools"]:
        tool = dict(tool)
        metadata = tool.pop("_meta", {})
        if metadata:
            assert set(metadata) == {TOOL_METADATA_KEY}
            tool["metadata"] = metadata[TOOL_METADATA_KEY]
        normalized.append(tool)
    baseline = json.loads(
        (Path(__file__).parent / "compatibility/v0.1.3-catalog-sha256.json").read_text()
    )
    assert len(normalized) == baseline["tools"] == 4
    assert (
        hashlib.sha256(
            json.dumps(normalized, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
        == baseline["sha256"]
    )
    assert (
        PROTOCOL in post(value, "server/discover").json()["result"]["supportedVersions"]
    )


def test_modern_prediction_preserves_upstream_batch_and_deprecation_contract(client):
    value, requests, created = client
    result = post(
        value,
        "tools/call",
        {
            "name": "predict_admet",
            "arguments": {
                "SMILES": [" CCO ", "CCN"],
                "feature": True,
                "uncertain": True,
            },
        },
    ).json()["result"]
    assert [(path, body) for path, body, _ in requests] == [
        ("/api/single/admet", {"SMILES": "CCO", "feature": True}),
        ("/api/single/admet", {"SMILES": "CCN", "feature": True}),
    ]
    assert len(created) == 1
    assert result["structuredContent"]["batchCount"] == 2
    assert "deprecated" in result["structuredContent"]["warnings"][0]
    assert result["_meta"]["sources"] == [legacy.ADMETLAB_SOURCE]
    assert result["content"][1]["text"].startswith("Source: ADMETlab 3.0")


def test_modern_outage_remains_visible_without_blocking_other_tools(client):
    value, _, _ = client
    result = post(
        value,
        "tools/call",
        {"name": "predict_admet", "arguments": {"SMILES": "fixture-unavailable"}},
    ).json()["result"]
    assert result["isError"] is True
    assert result["_meta"]["error"]["code"] == "upstream_unavailable"
    assert result["_meta"]["error"]["upstreamStatus"] == 500
    assert "structuredContent" not in result
    assert "synthetic provider exception" not in result["content"][0]["text"]
    assert value.get("/readyz").json()["status"] == "degraded"
    wash = post(
        value, "tools/call", {"name": "wash_molecule", "arguments": {"SMILES": "CCO"}}
    ).json()["result"]
    assert wash["structuredContent"] == {"washed": ["CCO"]}


def test_modern_invalid_arguments_do_not_reach_upstream(client):
    value, requests, _ = client
    for name, arguments, code in [
        ("wash_molecule", {}, -32602),
        ("unknown", {}, -32601),
        ("wash_molecule", {"SMILES": ["CCO"] * 1001}, -32602),
    ]:
        result = post(value, "tools/call", {"name": name, "arguments": arguments})
        assert result.json()["error"]["code"] == code
    assert requests == []
    assert post(value, "server/discover").status_code == 200


@pytest.mark.parametrize(
    "revision", ["2024-11-05", "2025-03-26", "2025-06-18", "2025-11-25"]
)
def test_released_legacy_handshake_and_aliases_remain(client, revision):
    value, _, _ = client
    response = value.post(
        "/mcp",
        headers={"MCP-Protocol-Version": revision},
        json={
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {"protocolVersion": revision},
        },
    )
    assert response.json()["result"]["protocolVersion"] == "2024-11-05"
    listed = value.post(
        "/mcp", json={"jsonrpc": "2.0", "id": 2, "method": "tools.list", "params": {}}
    )
    assert len(listed.json()["result"]["tools"]) == 4


@pytest.mark.parametrize(
    "headers",
    [
        {"Host": "attacker.example"},
        {"Origin": "https://attacker.example"},
        {"Mcp-Method": "tools/call"},
        {"MCP-Protocol-Version": "9999-01-01"},
    ],
)
def test_modern_transport_rejects_bad_authority_and_protocol(client, headers):
    value, requests, _ = client
    assert post(value, "tools/list", headers=headers).status_code in {400, 403, 421}
    assert requests == []
    assert post(value, "tools/list").status_code == 200


def test_streamed_body_limit_precedes_both_dispatchers(client):
    value, requests, _ = client
    response = value.post(
        "/mcp",
        content=iter([b" " * 600_000, b" " * 600_000]),
        headers={"Content-Type": "application/json"},
    )
    assert response.status_code == 413
    assert requests == []
    malformed = value.post("/mcp", content=b"{}", headers={"Content-Length": "invalid"})
    assert malformed.status_code == 400


def test_parallel_modern_calls_reuse_upstream_client_and_budget(client):
    value, requests, created = client
    smiles = ["CCO", "CCN", "CCC"]

    def call(item):
        response = post(
            value,
            "tools/call",
            {"name": "wash_molecule", "arguments": {"SMILES": item}},
        )
        assert response.json()["id"] == 7
        assert response.json()["result"]["structuredContent"] == {"washed": [item]}

    with ThreadPoolExecutor(max_workers=3) as pool:
        list(pool.map(call, smiles))
    assert len(created) == 1
    assert len(requests) == 3
    ordered = sorted(start for _, _, start in requests)
    assert all(right - left >= 0.18 for left, right in zip(ordered, ordered[1:]))
