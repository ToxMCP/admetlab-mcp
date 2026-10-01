from __future__ import annotations

import asyncio
import json
import time

import httpx
import pytest

from admetlab_mcp.client import admet_client as clients
from admetlab_mcp.settings import get_settings
from admetlab_mcp.transport import http as transport


@pytest.fixture(autouse=True)
def isolated_budget(monkeypatch):
    monkeypatch.setenv("ADMETLAB_RPS_LIMIT", "10")
    monkeypatch.setenv("ADMETLAB_BATCH_SIZE", "2")
    monkeypatch.setenv("ADMETLAB_RETRY_ATTEMPTS", "1")
    monkeypatch.setenv("ADMETLAB_RETRY_BACKOFF", "0")
    monkeypatch.setattr(clients, "_PROCESS_RATE_LIMITER", clients.UpstreamRateLimiter())
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def assert_spaced(starts: list[float]) -> None:
    ordered = sorted(starts)
    assert all(right - left >= 0.09 for left, right in zip(ordered, ordered[1:]))


def test_process_budget_survives_successive_event_loops() -> None:
    starts: list[float] = []

    async def run_clients():
        async def handler(request):
            starts.append(time.monotonic())
            return httpx.Response(200, json={"ok": True}, request=request)

        async def call():
            async with clients.AdmetClient(
                client=httpx.AsyncClient(
                    base_url="https://admet.example",
                    transport=httpx.MockTransport(handler),
                )
            ) as client:
                return await client.wash_molecule(["CCO"])

        await asyncio.gather(call(), call())

    asyncio.run(run_clients())
    asyncio.run(run_clients())
    assert len(starts) == 4
    assert_spaced(starts)


@pytest.mark.asyncio
async def test_multiple_clients_share_request_and_retry_budget() -> None:
    starts: list[float] = []

    async def handler(request):
        starts.append(time.monotonic())
        status = 429 if len(starts) == 1 else 200
        return httpx.Response(status, json={"ok": True}, request=request)

    upstreams = [
        httpx.AsyncClient(
            base_url="https://admet.example", transport=httpx.MockTransport(handler)
        )
        for _ in range(3)
    ]
    instances = [clients.AdmetClient(client=upstream) for upstream in upstreams]
    try:
        results = await asyncio.gather(
            *(client.wash_molecule(["CCO"]) for client in instances)
        )
    finally:
        await asyncio.gather(*(client.aclose() for client in instances))
    assert results == [{"ok": True}] * 3
    assert len(starts) == 4  # The retry consumes the same process-wide budget.
    assert_spaced(starts)


@pytest.mark.asyncio
async def test_concurrent_http_tools_reuse_lifespan_client_and_budget(
    monkeypatch,
) -> None:
    starts: list[float] = []
    created: list[clients.AdmetClient] = []

    async def handler(request):
        starts.append(time.monotonic())
        return httpx.Response(200, json={"ok": True}, request=request)

    upstream = httpx.AsyncClient(
        base_url="https://admet.example", transport=httpx.MockTransport(handler)
    )

    def factory():
        client = clients.AdmetClient(client=upstream)
        created.append(client)
        return client

    monkeypatch.setattr(transport, "AdmetClient", factory)
    async with transport.app.router.lifespan_context(transport.app):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=transport.app), base_url="http://mcp.test"
        ) as http_client:
            responses = await asyncio.gather(
                *(
                    http_client.post(
                        "/mcp",
                        json={
                            "jsonrpc": "2.0",
                            "id": index,
                            "method": "tools/call",
                            "params": {"name": name, "arguments": {"SMILES": "CCO"}},
                        },
                    )
                    for index, name in enumerate(
                        ["wash_molecule", "predict_admet", "render_molecule_svg"]
                    )
                )
            )
        assert len(created) == 1
        assert not upstream.is_closed
        assert all(
            response.status_code == 200 and "error" not in response.json()
            for response in responses
        )
        assert all(
            not response.json()["result"].get("isError") for response in responses
        )
    assert upstream.is_closed
    assert not hasattr(transport.app.state, "admet_client")
    assert len(starts) == 3
    assert_spaced(starts)


@pytest.mark.asyncio
@pytest.mark.parametrize("name", ["wash_molecule", "predict_admet"])
@pytest.mark.parametrize("smiles", [["CCO", "CCN", "CCC"], ["CCO", " ", ""]])
async def test_both_tools_reject_oversized_raw_batches_before_upstream(
    name, smiles
) -> None:
    captured: list[httpx.Request] = []

    async def handler(request):
        captured.append(request)
        return httpx.Response(200, json={"ok": True}, request=request)

    async with clients.AdmetClient(
        client=httpx.AsyncClient(
            base_url="https://admet.example", transport=httpx.MockTransport(handler)
        )
    ) as client:
        with pytest.raises(transport.RpcError, match="at most 2 SMILES") as error:
            await transport._handle_tools_call(
                {"name": name, "arguments": {"SMILES": smiles}}, client=client
            )
    assert error.value.code == -32602
    assert captured == []


@pytest.mark.asyncio
async def test_washing_at_limit_keeps_normalized_payload_and_catalog_limit() -> None:
    captured: list[httpx.Request] = []

    async def handler(request):
        captured.append(request)
        return httpx.Response(200, json={"washed": ["CCO", "CCN"]}, request=request)

    async with clients.AdmetClient(
        client=httpx.AsyncClient(
            base_url="https://admet.example", transport=httpx.MockTransport(handler)
        )
    ) as client:
        result = await transport._handle_tools_call(
            {"name": "wash_molecule", "arguments": {"SMILES": [" CCO ", "CCN"]}},
            client=client,
        )
    assert result["structuredContent"] == {"washed": ["CCO", "CCN"]}
    assert json.loads(captured[0].content) == {"SMILES": ["CCO", "CCN"]}
    by_name = {
        tool["name"]: tool for tool in (await transport._handle_tools_list())["tools"]
    }
    for name in ["wash_molecule", "predict_admet"]:
        assert by_name[name]["inputSchema"]["properties"]["SMILES"]["maxItems"] == 2
