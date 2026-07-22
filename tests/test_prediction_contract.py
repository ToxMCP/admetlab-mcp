from __future__ import annotations

import json
from typing import Any, Dict, Optional

import httpx
import pytest

from admetlab_mcp.client.admet_client import AdmetClient
from admetlab_mcp.transport import http as transport


class FakeAdmetClient:
    def __init__(
        self,
        *,
        response: Optional[Dict[str, Any]] = None,
        error: Optional[Exception] = None,
    ) -> None:
        self.response = response or {"ok": True}
        self.error = error
        self.calls: list[tuple[str, Optional[bool]]] = []

    async def __aenter__(self) -> "FakeAdmetClient":
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        return None

    async def predict_admet(
        self, smiles: str, feature: Optional[bool] = None
    ) -> Dict[str, Any]:
        self.calls.append((smiles, feature))
        if self.error:
            raise self.error
        return {**self.response, "smiles": smiles}


def _reset_prediction_status() -> None:
    transport._prediction_availability.update(
        {"status": "unknown", "lastChecked": None, "upstreamStatus": None}
    )


def _http_error(status: int, message: str) -> httpx.HTTPStatusError:
    request = httpx.Request("POST", "https://admetlab3.scbdd.com/api/single/admet")
    response = httpx.Response(status, request=request, text=message)
    return httpx.HTTPStatusError(message, request=request, response=response)


@pytest.mark.asyncio
async def test_client_sends_single_smiles_contract() -> None:
    captured: list[httpx.Request] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        return httpx.Response(200, request=request, json={"taskid": "task-1"})

    async_client = httpx.AsyncClient(
        base_url="https://admetlab3.scbdd.com",
        transport=httpx.MockTransport(handler),
    )
    client = AdmetClient(client=async_client)
    try:
        result = await client.predict_admet("CCO", feature=True)
    finally:
        await client.aclose()

    assert result == {"taskid": "task-1"}
    assert captured[0].url.path == "/api/single/admet"
    assert json.loads(captured[0].content) == {"SMILES": "CCO", "feature": True}


@pytest.mark.asyncio
async def test_predict_calls_upstream_once_per_smiles_and_warns_on_uncertain(
    monkeypatch,
) -> None:
    _reset_prediction_status()
    fake = FakeAdmetClient(response={"prediction": "ok"})
    monkeypatch.setattr(transport, "AdmetClient", lambda: fake)

    result = await transport._handle_tools_call(
        {
            "name": "predict_admet",
            "arguments": {
                "SMILES": ["CCO", "CCN"],
                "feature": True,
                "uncertain": True,
            },
        }
    )

    assert fake.calls == [("CCO", True), ("CCN", True)]
    assert result["structuredContent"]["batchCount"] == 2
    assert len(result["structuredContent"]["batches"]) == 2
    assert "deprecated" in result["structuredContent"]["warnings"][0]
    assert result["_meta"]["sources"][0]["name"] == "ADMETlab 3.0"
    assert transport._prediction_availability["status"] == "available"


@pytest.mark.asyncio
async def test_upstream_500_returns_tool_error_and_degraded_readiness(
    monkeypatch,
) -> None:
    _reset_prediction_status()
    fake = FakeAdmetClient(error=_http_error(500, "KeyError: BSEP traceback"))
    monkeypatch.setattr(transport, "AdmetClient", lambda: fake)

    result = await transport._handle_tools_call(
        {"name": "predict_admet", "arguments": {"SMILES": "CCO"}}
    )
    readiness = await transport.readiness()

    assert result["isError"] is True
    assert "structuredContent" not in result
    assert result["_meta"]["error"]["code"] == "upstream_unavailable"
    assert result["_meta"]["error"]["upstreamStatus"] == 500
    assert "BSEP" not in result["content"][0]["text"]
    assert readiness["status"] == "degraded"
    assert readiness["prediction"]["status"] == "degraded"
    assert readiness["otherTools"] == "available"


@pytest.mark.asyncio
async def test_upstream_422_returns_actionable_rejection_without_outage(
    monkeypatch,
) -> None:
    _reset_prediction_status()
    fake = FakeAdmetClient(error=_http_error(422, "validation detail"))
    monkeypatch.setattr(transport, "AdmetClient", lambda: fake)

    result = await transport._handle_tools_call(
        {"name": "predict_admet", "arguments": {"SMILES": "not-smiles"}}
    )

    assert result["isError"] is True
    assert result["_meta"]["error"]["code"] == "upstream_rejected"
    assert result["_meta"]["error"]["retryable"] is False
    assert "Check each SMILES" in result["content"][0]["text"]
    assert transport._prediction_availability["status"] == "unknown"
