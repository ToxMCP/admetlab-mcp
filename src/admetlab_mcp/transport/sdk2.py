"""SDK2 transport adapter preserving ADMETlab's existing upstream contracts."""

from __future__ import annotations

import sys
from typing import Any

import anyio
from mcp.server.caching import CacheHint
from mcp.server.context import ServerRequestContext
from mcp.server.lowlevel import Server
from mcp.server.stdio import stdio_server
from mcp.server.transport_security import TransportSecuritySettings
from mcp.shared.exceptions import MCPError
from mcp_types import (
    PROTOCOL_VERSION_META_KEY,
    CallToolRequestParams,
    CallToolResult,
    ErrorData,
    ListToolsResult,
    PaginatedRequestParams,
    Tool,
    ToolAnnotations,
)
from starlette.requests import Request
from starlette.responses import Response
from starlette.types import ASGIApp, Receive, Scope, Send

from .. import __version__
from ..client.admet_client import AdmetClient
from ..logging import configure_logging
from ..settings import get_settings

TOOL_METADATA_KEY = "org.toxmcp/toolMetadata"
LEGACY_HTTP_REVISIONS = {"2024-11-05", "2025-03-26", "2025-06-18", "2025-11-25"}


def is_modern_request(request: Request, payload: dict[str, Any]) -> bool:
    revision = request.headers.get("mcp-protocol-version")
    if revision and revision not in LEGACY_HTTP_REVISIONS:
        return True
    params = payload.get("params")
    metadata = params.get("_meta") if isinstance(params, dict) else None
    return isinstance(metadata, dict) and PROTOCOL_VERSION_META_KEY in metadata


def create_sdk_server(client: AdmetClient) -> Server:
    from . import http as legacy

    async def list_tools(
        context: ServerRequestContext, params: PaginatedRequestParams | None
    ) -> ListToolsResult:
        tools = []
        for item in legacy._tool_registry(get_settings()):
            tools.append(
                Tool(
                    name=item["name"],
                    description=item["description"],
                    input_schema=item["inputSchema"],
                    annotations=ToolAnnotations.model_validate(item["annotations"]),
                    meta=(
                        {TOOL_METADATA_KEY: item["metadata"]}
                        if "metadata" in item
                        else None
                    ),
                )
            )
        return ListToolsResult(tools=tools)

    async def call_tool(
        context: ServerRequestContext, params: CallToolRequestParams
    ) -> CallToolResult:
        try:
            result = await legacy._handle_tools_call(
                {"name": params.name, "arguments": params.arguments or {}},
                client=client,
            )
        except legacy.RpcError as error:
            raise MCPError.from_error_data(
                ErrorData(code=error.code, message=error.message, data=error.data)
            ) from error
        return CallToolResult.model_validate(result)

    return Server(
        "admetlab-mcp",
        version=__version__,
        instructions="Preserve the ADMETlab source label in answers. If predict_admet returns isError=true, explain that the upstream prediction service is unavailable rather than presenting a prediction.",
        on_list_tools=list_tools,
        on_call_tool=call_tool,
        cache_hints={"tools/list": CacheHint(ttl_ms=60_000, scope="private")},
    )


def create_sdk_http_app(client: AdmetClient):
    settings = get_settings()
    return create_sdk_server(client).streamable_http_app(
        json_response=True,
        stateless_http=True,
        max_request_body_size=settings.max_request_bytes,
        transport_security=TransportSecuritySettings(
            enable_dns_rebinding_protection=True,
            allowed_hosts=settings.allowed_hosts
            or ["localhost:*", "127.0.0.1:*", "[::1]:*"],
            allowed_origins=settings.allowed_origins
            or ["http://localhost:*", "http://127.0.0.1:*", "http://[::1]:*"],
        ),
    )


class SDKResponse(Response):
    def __init__(self, application: ASGIApp, body: bytes):
        super().__init__()
        self.application = application
        self.body = body

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        supplied = False

        async def replay():
            nonlocal supplied
            if not supplied:
                supplied = True
                return {"type": "http.request", "body": self.body, "more_body": False}
            return await receive()

        await self.application(scope, replay, send)


async def serve_stdio() -> None:
    # Logs must never share stdout with the stdio JSON-RPC protocol.
    configure_logging(stream=sys.stderr)
    from . import http as legacy

    async with legacy.AdmetClient() as client:
        server = create_sdk_server(client)
        async with stdio_server() as (reader, writer):
            await server.run(reader, writer, server.create_initialization_options())


def main() -> None:
    anyio.run(serve_stdio)


if __name__ == "__main__":
    main()
