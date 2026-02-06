from __future__ import annotations

import asyncio
import time
from typing import Any, Dict, Iterable, List, Optional

import httpx

from ..settings import get_settings


class AdmetClient:
    def __init__(self, client: Optional[httpx.AsyncClient] = None) -> None:
        self.settings = get_settings()
        self._client = client or httpx.AsyncClient(
            base_url=str(self.settings.base_url),
            timeout=self.settings.timeout_seconds,
            headers=self._default_headers(),
        )
        self._lock = asyncio.Lock()
        self._last_request_ts = 0.0

    def _default_headers(self) -> Dict[str, str]:
        headers: Dict[str, str] = {"Content-Type": "application/json"}
        if self.settings.api_key:
            headers["X-API-KEY"] = self.settings.api_key
        return headers

    async def _throttle(self) -> None:
        async with self._lock:
            now = time.monotonic()
            min_interval = 1.0 / float(self.settings.rps_limit)
            elapsed = now - self._last_request_ts
            if elapsed < min_interval:
                await asyncio.sleep(min_interval - elapsed)
            self._last_request_ts = time.monotonic()

    async def _post_json(self, path: str, json_body: Dict[str, Any]) -> httpx.Response:
        attempt = 0
        last_exc: Optional[Exception] = None
        while attempt <= self.settings.retry_attempts:
            try:
                await self._throttle()
                resp = await self._client.post(path, json=json_body)
                if resp.status_code in {429, 500, 502, 503, 504}:
                    raise httpx.HTTPStatusError(
                        f"Upstream error {resp.status_code}", request=resp.request, response=resp
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

    async def render_molecule_svg(self, smiles: str, figsize: Optional[List[int]] = None) -> Dict[str, Any]:
        payload: Dict[str, Any] = {"SMILES": smiles}
        if figsize:
            payload["figsize"] = figsize
        resp = await self._post_json("/api/molsvg", payload)
        resp.raise_for_status()
        return resp.json()

    async def predict_admet(
        self,
        smiles: Iterable[str],
        feature: Optional[bool] = None,
        uncertain: Optional[bool] = None,
    ) -> Dict[str, Any]:
        payload: Dict[str, Any] = {"SMILES": list(smiles)}
        payload["feature"] = self.settings.feature_default if feature is None else feature
        payload["uncertain"] = self.settings.uncertain_default if uncertain is None else uncertain
        endpoints = [self.settings.admet_endpoint, *self.settings.admet_fallback_endpoints]
        seen: Dict[str, str] = {}
        for path in endpoints:
            try:
                resp = await self._post_json(path, payload)
                resp.raise_for_status()
                return resp.json()
            except httpx.HTTPStatusError as exc:
                seen[path] = f"{exc.response.status_code} {exc.response.reason_phrase}"
            except httpx.TimeoutException as exc:
                seen[path] = f"timeout: {exc}"
        raise httpx.HTTPError(f"All ADMET endpoints failed: {seen}")

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
