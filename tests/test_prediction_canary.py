from __future__ import annotations

import asyncio
import json

import httpx
import pytest

from admetlab_mcp import canary
from admetlab_mcp.client import admet_client as clients


@pytest.fixture(autouse=True)
def isolated_budget(monkeypatch):
    monkeypatch.setattr(clients, "_PROCESS_RATE_LIMITER", clients.UpstreamRateLimiter())


@pytest.mark.asyncio
async def test_canary_pins_one_ethanol_request_without_credentials_and_closes_client(
    monkeypatch,
):
    captured: list[httpx.Request] = []

    async def handler(request):
        captured.append(request)
        return httpx.Response(
            200, request=request, json={"taskid": "do-not-print-provider-data"}
        )

    upstream = httpx.AsyncClient(
        base_url="https://admetlab3.scbdd.com", transport=httpx.MockTransport(handler)
    )
    monkeypatch.setenv("ADMETLAB_API_KEY", "must-not-be-sent")
    monkeypatch.setenv("ADMETLAB_BASE_URL", "https://wrong.example")
    monkeypatch.setenv("ADMETLAB_RETRY_ATTEMPTS", "10")
    settings_seen = []

    def factory(*, settings):
        settings_seen.append(settings)
        return clients.AdmetClient(settings=settings, client=upstream)

    monkeypatch.setattr(canary, "AdmetClient", factory)
    report = await canary.run_prediction_canary()
    assert report["passed"] is True
    assert report["scientificQualification"] == "not_assessed"
    assert "do-not-print-provider-data" not in json.dumps(report)
    assert upstream.is_closed
    assert len(captured) == 1
    assert captured[0].url.path == "/api/single/admet"
    assert json.loads(captured[0].content) == {"SMILES": "CCO", "feature": False}
    assert "x-api-key" not in captured[0].headers
    assert settings_seen[0].retry_attempts == 0
    assert settings_seen[0].rps_limit == 1
    assert settings_seen[0].api_key is None
    assert str(settings_seen[0].base_url) == "https://admetlab3.scbdd.com/"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "status,payload,expected",
    [
        (429, {"trace": "must-not-print"}, "provider_rate_limited"),
        (503, {"trace": "must-not-print"}, "provider_unavailable"),
        (422, {"detail": "must-not-print"}, "provider_rejected"),
        (200, {"error": "must-not-print"}, "unexpected_response"),
        (200, {"success": False}, "unexpected_response"),
        (200, {"status": "error", "message": "must-not-print"}, "unexpected_response"),
        (200, {}, "unexpected_response"),
        (200, ["must-not-print"], "unexpected_response"),
    ],
)
async def test_canary_surfaces_failures_without_retries_or_provider_bodies(
    status, payload, expected
):
    captured = []

    async def handler(request):
        captured.append(request)
        return httpx.Response(status, request=request, json=payload)

    async with clients.AdmetClient(
        settings=canary._canary_settings(),
        client=httpx.AsyncClient(
            base_url="https://mock.example", transport=httpx.MockTransport(handler)
        ),
    ) as client:
        report = await canary.run_prediction_canary(client=client)
    assert len(captured) == 1
    assert report["passed"] is False
    assert report["status"] == expected
    assert "must-not-print" not in json.dumps(report)


@pytest.mark.asyncio
async def test_canary_rejects_non_json_response_without_printing_it():
    async def handler(request):
        return httpx.Response(200, request=request, text="must-not-print provider HTML")

    async with clients.AdmetClient(
        settings=canary._canary_settings(),
        client=httpx.AsyncClient(
            base_url="https://mock.example", transport=httpx.MockTransport(handler)
        ),
    ) as client:
        report = await canary.run_prediction_canary(client=client)
    assert report["status"] == "invalid_json"
    assert report["passed"] is False
    assert "must-not-print" not in json.dumps(report)


@pytest.mark.asyncio
async def test_canary_total_deadline_cancels_a_stalled_provider(monkeypatch):
    captured = []
    cancelled = []

    async def handler(request):
        captured.append(request)
        try:
            await asyncio.Event().wait()
        finally:
            cancelled.append(True)

    monkeypatch.setattr(canary, "CANARY_TIMEOUT_SECONDS", 0.02)
    async with clients.AdmetClient(
        settings=canary._canary_settings(),
        client=httpx.AsyncClient(
            base_url="https://mock.example", transport=httpx.MockTransport(handler)
        ),
    ) as client:
        report = await canary.run_prediction_canary(client=client)
    assert report["status"] == "timeout"
    assert report["passed"] is False
    assert len(captured) == 1
    assert cancelled == [True]


def test_canary_command_fails_clearly_on_provider_rate_limit(monkeypatch, capsys):
    async def unavailable():
        return {"status": "provider_rate_limited", "httpStatus": 429, "passed": False}

    monkeypatch.setattr(canary, "run_prediction_canary", unavailable)
    assert canary.main() == 1
    report = json.loads(capsys.readouterr().out)
    assert report["status"] == "provider_rate_limited"
    assert report["httpStatus"] == 429
