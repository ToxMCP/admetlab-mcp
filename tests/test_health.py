import json
from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient

from admetlab_mcp.transport.http import app


def test_health_endpoint():
    client = TestClient(app)
    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"


def test_tools_list():
    client = TestClient(app)
    payload = {"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}}
    resp = client.post("/mcp", json=payload)
    assert resp.status_code == 200
    data = resp.json()
    assert "tools" in data["result"]


def test_initialize_advertises_tools_capability_as_object():
    client = TestClient(app)
    payload = {
        "jsonrpc": "2.0",
        "id": 2,
        "method": "initialize",
        "params": {
            "protocolVersion": "2025-06-18",
            "capabilities": {},
            "clientInfo": {"name": "test-client", "version": "1.0"},
        },
    }
    resp = client.post("/mcp", json=payload)
    assert resp.status_code == 200
    result = resp.json()["result"]
    assert result["capabilities"]["tools"] == {}
    assert result["serverInfo"]["version"] == "0.1.2"
    assert "Preserve the ADMETlab source label" in result["instructions"]


def test_initialized_notification_returns_accepted_without_body():
    client = TestClient(app)
    payload = {
        "jsonrpc": "2.0",
        "method": "notifications/initialized",
        "params": {},
    }
    resp = client.post("/mcp", json=payload)
    assert resp.status_code == 202
    assert resp.content == b""


@patch(
    "admetlab_mcp.transport.http._call_tool",
    new_callable=AsyncMock,
)
def test_tools_call_returns_standard_content_blocks(mock_call_tool):
    mock_call_tool.return_value = {"washed": ["CCO"]}
    client = TestClient(app)
    payload = {
        "jsonrpc": "2.0",
        "id": 3,
        "method": "tools/call",
        "params": {"name": "wash_molecule", "arguments": {"SMILES": "CCO"}},
    }
    resp = client.post("/mcp", json=payload)
    assert resp.status_code == 200
    result = resp.json()["result"]
    assert result["structuredContent"] == {"washed": ["CCO"]}
    assert result["content"][0]["type"] == "text"
    assert json.loads(result["content"][0]["text"]) == {"washed": ["CCO"]}
    assert result["content"][1]["text"].startswith("Source: ADMETlab 3.0")
    assert result["_meta"]["sources"][0]["name"] == "ADMETlab 3.0"


def test_tools_call_rejects_missing_smiles():
    client = TestClient(app)
    payload = {
        "jsonrpc": "2.0",
        "id": 2,
        "method": "tools/call",
        "params": {"name": "wash_molecule", "arguments": {}},
    }
    resp = client.post("/mcp", json=payload)
    assert resp.status_code == 200
    data = resp.json()
    assert data["error"]["code"] == -32602


def test_invalid_request_requires_method_string():
    client = TestClient(app)
    payload = {"jsonrpc": "2.0", "id": 3, "method": None, "params": {}}
    resp = client.post("/mcp", json=payload)
    assert resp.status_code == 200
    data = resp.json()
    assert data["error"]["code"] == -32600
