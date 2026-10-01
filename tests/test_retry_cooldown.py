from __future__ import annotations

import asyncio
import time
from datetime import datetime, timedelta, timezone
from email.utils import format_datetime

import httpx
import pytest
from pydantic import ValidationError

from admetlab_mcp.client import admet_client as clients
from admetlab_mcp.settings import Settings, get_settings


@pytest.fixture(autouse=True)
def isolated_budget(monkeypatch):
    monkeypatch.setattr(clients, "_PROCESS_RATE_LIMITER", clients.UpstreamRateLimiter())
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def test_retry_after_seconds_dates_and_bounds():
    now = datetime(2026, 10, 1, tzinfo=timezone.utc)
    assert clients._retry_after_delay(" 12 ", now=now) == 12
    assert clients._retry_after_delay("0", now=now) == 0
    assert clients._retry_after_delay("999999999999999", now=now) == 30
    assert (
        clients._retry_after_delay(format_datetime(now + timedelta(seconds=8)), now=now)
        == 8
    )
    assert (
        clients._retry_after_delay(format_datetime(now + timedelta(days=365)), now=now)
        == 30
    )
    assert (
        clients._retry_after_delay(format_datetime(now - timedelta(seconds=8)), now=now)
        == 0
    )
    for invalid in [None, "", "not-a-date", "-10", "NaN", "Inf", "1.5", "9" * 5000]:
        assert clients._retry_after_delay(invalid, now=now) is None


def test_retry_configuration_and_exponential_delays_are_bounded():
    assert Settings(_env_file=None, retry_attempts=0).retry_attempts == 0
    for kwargs in [
        {"retry_attempts": -1},
        {"retry_attempts": 11},
        {"retry_backoff": -1},
        {"retry_backoff": 31},
        {"retry_backoff": float("nan")},
        {"retry_backoff": float("inf")},
    ]:
        with pytest.raises(ValidationError):
            Settings(_env_file=None, **kwargs)
    assert [clients._retry_backoff(0.5, n) for n in range(1, 11)] == [
        0.5,
        1,
        2,
        4,
        8,
        16,
        30,
        30,
        30,
        30,
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [429, 503])
async def test_cooldown_survives_exhausted_retries_and_spaces_other_clients(status):
    starts: list[float] = []

    async def handler(request):
        starts.append(time.monotonic())
        return httpx.Response(
            status if len(starts) == 1 else 200,
            request=request,
            headers={"Retry-After": "1"},
            json={"ok": True},
        )

    settings = Settings(_env_file=None, retry_attempts=0, retry_backoff=0, rps_limit=10)
    instances = [
        clients.AdmetClient(
            settings=settings,
            client=httpx.AsyncClient(
                base_url="https://mock.example", transport=httpx.MockTransport(handler)
            ),
        )
        for _ in range(4)
    ]
    try:
        with pytest.raises(httpx.HTTPStatusError):
            await instances[0].wash_molecule(["CCO"])
        results = await asyncio.wait_for(
            asyncio.gather(
                *(client.wash_molecule(["CCN"]) for client in instances[1:])
            ),
            timeout=2,
        )
        assert results == [{"ok": True}] * 3
    finally:
        await asyncio.gather(*(client.aclose() for client in instances))
    assert len(starts) == 4
    assert starts[1] - starts[0] >= 0.95  # Even the no-retry caller cooled everyone.
    assert all(right - left >= 0.09 for left, right in zip(starts[1:], starts[2:]))


@pytest.mark.asyncio
async def test_retry_honors_header_and_does_not_short_circuit_process_budget():
    starts: list[float] = []

    async def handler(request):
        starts.append(time.monotonic())
        return httpx.Response(
            503 if len(starts) == 1 else 200,
            request=request,
            headers={"Retry-After": "1"},
            json={"ok": True},
        )

    async with clients.AdmetClient(
        settings=Settings(
            _env_file=None, retry_attempts=1, retry_backoff=0.05, rps_limit=10
        ),
        client=httpx.AsyncClient(
            base_url="https://mock.example", transport=httpx.MockTransport(handler)
        ),
    ) as client:
        result = await asyncio.wait_for(client.wash_molecule(["CCO"]), timeout=2)
    assert result == {"ok": True}
    assert len(starts) == 2
    assert starts[1] - starts[0] >= 0.95


@pytest.mark.asyncio
async def test_cancelling_cooldown_waiters_does_not_reserve_future_slots():
    limiter = clients.UpstreamRateLimiter()
    limiter.defer(0.1)
    initial_deadline = limiter._next_request_ts
    limiter.defer(0)  # A shorter provider response must not release earlier waiters.
    assert limiter._next_request_ts == initial_deadline
    waiting = [asyncio.create_task(limiter.acquire(100)) for _ in range(20)]
    await asyncio.sleep(0.005)
    for task in waiting:
        task.cancel()
    await asyncio.gather(*waiting, return_exceptions=True)
    started = time.monotonic()
    await asyncio.wait_for(limiter.acquire(100), timeout=0.2)
    assert time.monotonic() - started < 0.15


def test_provider_cooldown_has_a_hard_upper_bound():
    limiter = clients.UpstreamRateLimiter()
    before = time.monotonic()
    limiter.defer(1000000)
    assert before + 30 <= limiter._next_request_ts <= time.monotonic() + 30
