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
