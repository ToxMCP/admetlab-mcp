"""Offline test launcher exercising the real upstream client with synthetic responses."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

import httpx


def install_mock_upstream():
    os.environ["ADMETLAB_RETRY_ATTEMPTS"] = "0"
    os.environ["ADMETLAB_RETRY_BACKOFF"] = "0"
    # Keep catalog defaults unchanged, including rps=5 and batch_size=1000.
    from admetlab_mcp.client.admet_client import AdmetClient
    from admetlab_mcp.settings import get_settings
    from admetlab_mcp.transport import http as legacy

    get_settings.cache_clear()

    async def handler(request):
        body = json.loads(request.content)
        captured = os.environ.get("ADMETLAB_COMPATIBILITY_REQUEST_LOG")
        if captured:
            with Path(captured).open("a") as stream:
                stream.write(
                    json.dumps({"path": request.url.path, "body": body}, sort_keys=True)
                    + "\n"
                )
        if request.url.path == "/api/washmol":
            return httpx.Response(200, request=request, json={"washed": body["SMILES"]})
        if request.url.path == "/api/molsvg":
            return httpx.Response(
                200,
                request=request,
                json={
                    "svg": "<svg>" + body["SMILES"] + "</svg>",
                    "figsize": body.get("figsize"),
                },
            )
        if request.url.path == "/api/single/admet":
            status = (
                500
                if body["SMILES"] == "fixture-unavailable"
                else 422 if body["SMILES"] == "fixture-rejected" else 200
            )
            return httpx.Response(
                status,
                request=request,
                json={
                    "syntheticFixture": True,
                    "taskid": "fixture-" + body["SMILES"],
                    "feature": body["feature"],
                },
            )
        if request.url.path == "/api/admetCSV":
            return httpx.Response(
                200,
                request=request,
                headers={"Content-Type": "text/csv"},
                text="taskId,fixture\n" + body["taskId"] + ",true\n",
            )
        raise AssertionError(f"Unexpected upstream path: {request.url.path}")

    def factory():
        return AdmetClient(
            client=httpx.AsyncClient(
                base_url="https://admetlab3.scbdd.com",
                transport=httpx.MockTransport(handler),
            )
        )

    legacy.AdmetClient = factory


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--transport", choices=["http", "stdio"], required=True)
    parser.add_argument("--port", type=int)
    args = parser.parse_args()
    install_mock_upstream()
    if args.transport == "http":
        import uvicorn

        from admetlab_mcp.transport.http import app

        uvicorn.run(app, host="127.0.0.1", port=args.port, log_level="warning")
    else:
        from admetlab_mcp.transport.sdk2 import main as serve

        serve()


if __name__ == "__main__":
    main()
