from __future__ import annotations

import asyncio
import time
from threading import Lock
from typing import Any, Dict, Iterable, List, Optional

import httpx

from ..settings import get_settings


class UpstreamRateLimiter:
    """Space request starts across clients and event loops within one process."""

    def __init__(self) -> None:
        self._lock = Lock()
        self._next_request_ts = 0.0

    async def acquire(self, rps_limit: int) -> None:
        while True:
            # Hold a synchronous lock only for the timestamp update, never while waiting.
            with self._lock:
                now = time.monotonic()
                delay = self._next_request_ts - now
                if delay <= 0:
                    self._next_request_ts = now + 1.0 / rps_limit
                    return
            await asyncio.sleep(delay)


_PROCESS_RATE_LIMITER = UpstreamRateLimiter()


class AdmetClient:
    def __init__(self, client: Optional[httpx.AsyncClient] = None) -> None:
        self.settings = get_settings()
        self._client = client or httpx.AsyncClient(
            base_url=str(self.settings.base_url),
            timeout=self.settings.timeout_seconds,
            headers=self._default_headers(),
        )
        self._rate_limiter = _PROCESS_RATE_LIMITER

    def _default_headers(self) -> Dict[str, str]:
        headers: Dict[str, str] = {"Content-Type": "application/json"}
        if self.settings.api_key:
            headers["X-API-KEY"] = self.settings.api_key
        return headers

    async def _throttle(self) -> None:
        await self._rate_limiter.acquire(self.settings.rps_limit)

    async def _post_json(self, path: str, json_body: Dict[str, Any]) -> httpx.Response:
        attempt = 0
        last_exc: Optional[Exception] = None
        while attempt <= self.settings.retry_attempts:
            try:
                await self._throttle()
                resp = await self._client.post(path, json=json_body)
                if resp.status_code in {429, 500, 502, 503, 504}:
                    raise httpx.HTTPStatusError(
                        f"Upstream error {resp.status_code}",
                        request=resp.request,
                        response=resp,
                    )
                return resp
            except (httpx.TimeoutException, httpx.HTTPStatusError) as exc:
                last_exc = exc
                attempt += 1
                if attempt > self.settings.retry_attempts:
                    break
                backoff = self.settings.retry_backoff * (2 ** (attempt - 1))
                await asyncio.sleep(backoff)
        if last_exc:
            raise last_exc
        raise RuntimeError("Request failed without exception context")

    async def wash_molecule(self, smiles: Iterable[str]) -> Dict[str, Any]:
        payload = {"SMILES": list(smiles)}
        resp = await self._post_json("/api/washmol", payload)
        resp.raise_for_status()
        return resp.json()

    async def render_molecule_svg(
        self, smiles: str, figsize: Optional[List[int]] = None
    ) -> Dict[str, Any]:
        payload: Dict[str, Any] = {"SMILES": smiles}
        if figsize:
            payload["figsize"] = figsize
        resp = await self._post_json("/api/molsvg", payload)
        resp.raise_for_status()
        return resp.json()

    async def predict_admet(
        self,
        smiles: str,
        feature: Optional[bool] = None,
    ) -> Dict[str, Any]:
        payload: Dict[str, Any] = {"SMILES": smiles}
        payload["feature"] = (
            self.settings.feature_default if feature is None else feature
        )
        resp = await self._post_json(self.settings.admet_endpoint, payload)
        resp.raise_for_status()
        return resp.json()

    async def fetch_admet_csv(self, task_id: str) -> httpx.Response:
        payload = {"taskId": task_id}
        resp = await self._post_json("/api/admetCSV", payload)
        resp.raise_for_status()
        return resp

    async def aclose(self) -> None:
        await self._client.aclose()

    async def __aenter__(self) -> "AdmetClient":
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        await self.aclose()
