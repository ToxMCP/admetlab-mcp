"""One bounded, technical-only upstream prediction check; never print provider data."""

from __future__ import annotations

import asyncio
import json
from contextlib import AsyncExitStack
from typing import Any, Optional

import httpx

from .client.admet_client import AdmetClient
from .settings import Settings

CANARY_TIMEOUT_SECONDS = 20


def _canary_settings() -> Settings:
    # No dotenv, credentials, endpoint overrides, or retries in the public canary.
    return Settings(
        _env_file=None,
        base_url="https://admetlab3.scbdd.com",
        admet_endpoint="/api/single/admet",
        timeout_seconds=15,
        retry_attempts=0,
        rps_limit=1,
        batch_size=1,
        feature_default=False,
        api_key=None,
    )


async def run_prediction_canary(
    *, client: Optional[AdmetClient] = None
) -> dict[str, Any]:
    report: dict[str, Any] = {
        "check": "single ethanol prediction endpoint: HTTP and JSON availability only",
        "scientificQualification": "not_assessed",
    }
    try:
        async with AsyncExitStack() as stack:
            if client is None:
                client = await stack.enter_async_context(
                    AdmetClient(settings=_canary_settings())
                )
            result = await asyncio.wait_for(
                client.predict_admet("CCO", feature=False),
                timeout=CANARY_TIMEOUT_SECONDS,
            )
    except httpx.HTTPStatusError as exc:
        status = exc.response.status_code
        report.update(
            status=(
                "provider_rate_limited"
                if status == 429
                else "provider_unavailable" if status >= 500 else "provider_rejected"
            ),
            httpStatus=status,
            passed=False,
        )
    except (asyncio.TimeoutError, httpx.TimeoutException):
        report.update(status="timeout", passed=False)
    except httpx.HTTPError:
        report.update(status="transport_error", passed=False)
    except (json.JSONDecodeError, UnicodeDecodeError):
        report.update(status="invalid_json", passed=False)
    else:
        # The client exposes the provider's raw JSON, with no governed prediction
        # response schema. Check only that it responds with a nonempty object and
        # does not explicitly report an error; do not assert scientific validity.
        error_status = (
            isinstance(result, dict)
            and isinstance(result.get("status"), str)
            and result["status"].strip().lower() in {"error", "failed", "failure"}
        )
        valid = (
            isinstance(result, dict)
            and bool(result)
            and not any(result.get(key) for key in ("error", "errors", "detail"))
            and result.get("success") is not False
            and not error_status
        )
        report.update(
            status="http_json_ok" if valid else "unexpected_response", passed=valid
        )
    return report


def main() -> int:
    try:
        report = asyncio.run(run_prediction_canary())
    except Exception:
        # Keep credentials and upstream exception/traceback bodies out of CI output.
        report = {"status": "canary_error", "passed": False}
    print(json.dumps(report, sort_keys=True))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
