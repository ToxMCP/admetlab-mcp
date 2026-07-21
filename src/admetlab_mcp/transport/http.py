from __future__ import annotations

import json
import logging
import math
from typing import Any, Dict, List, Optional

import httpx
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, Response

from .. import __version__
from ..client.admet_client import AdmetClient
from ..logging import configure_logging, correlation_id
from ..settings import get_settings

app = FastAPI(title="ADMETlab 3.0 MCP Server", version=__version__)
logger = logging.getLogger(__name__)


class RpcError(Exception):
    def __init__(
        self,
        code: int,
        message: str,
        *,
        data: Optional[Dict[str, Any]] = None,
        status_code: int = 200,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.data = data
        self.status_code = status_code


@app.on_event("startup")
async def startup_event() -> None:
    configure_logging()


@app.get("/health")
async def health() -> Dict[str, str]:
    return {"status": "ok"}


def _tool_registry(settings) -> List[Dict[str, Any]]:
    return [
        {
            "name": "wash_molecule",
            "description": "Standardize molecules via /api/washmol. Input SMILES string or list.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "SMILES": {"type": ["string", "array"], "items": {"type": "string"}}
                },
                "required": ["SMILES"],
            },
        },
        {
            "name": "render_molecule_svg",
            "description": "Render molecule SVG via /api/molsvg.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "SMILES": {"type": "string"},
                    "figsize": {
                        "type": "array",
                        "items": {"type": "integer"},
                        "maxItems": 2,
                        "minItems": 2,
                    },
                },
                "required": ["SMILES"],
            },
        },
        {
            "name": "predict_admet",
            "description": "Predict ADMET properties via /api/admet with optional feature/uncertain flags.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "SMILES": {
                        "type": ["array", "string"],
                        "items": {"type": "string"},
                    },
                    "feature": {"type": "boolean"},
                    "uncertain": {"type": "boolean"},
                },
                "required": ["SMILES"],
            },
            "metadata": {
                "batch_size": settings.batch_size,
                "rps_limit": settings.rps_limit,
            },
        },
        {
            "name": "fetch_admet_csv",
            "description": "Fetch CSV results for an ADMET task via /api/admetCSV using taskId.",
            "inputSchema": {
                "type": "object",
                "properties": {"taskId": {"type": "string"}},
                "required": ["taskId"],
            },
        },
    ]


async def _handle_initialize(_params: Dict[str, Any]) -> Dict[str, Any]:
    settings = get_settings()
    return {
        "protocolVersion": "2024-11-05",
        "capabilities": {"tools": {}},
        "serverInfo": {"name": "admetlab-mcp", "version": app.version},
        "metadata": {
            "rpsLimit": settings.rps_limit,
            "batchSize": settings.batch_size,
            "baseUrl": str(settings.base_url),
        },
    }


async def _handle_tools_list() -> Dict[str, Any]:
    settings = get_settings()
    return {"tools": _tool_registry(settings)}


def _chunk_smiles(smiles: List[str], batch_size: int) -> List[List[str]]:
    if not smiles:
        return []
    batches = int(math.ceil(len(smiles) / float(batch_size)))
    return [smiles[i * batch_size : (i + 1) * batch_size] for i in range(batches)]


def _rpc_error_payload(
    *,
    rpc_id: Any,
    code: int,
    message: str,
    data: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    error: Dict[str, Any] = {"code": code, "message": message}
    if data is not None:
        error["data"] = data
    return {"jsonrpc": "2.0", "id": rpc_id, "error": error}


def _coerce_smiles(smiles_arg: Any) -> List[str]:
    candidates: List[Any]
    if isinstance(smiles_arg, str):
        candidates = [smiles_arg]
    elif isinstance(smiles_arg, list):
        candidates = smiles_arg
    else:
        raise RpcError(
            -32602,
            "Invalid params: SMILES must be a string or an array of strings",
        )

    normalized: List[str] = []
    for candidate in candidates:
        if not isinstance(candidate, str):
            raise RpcError(
                -32602,
                "Invalid params: each SMILES value must be a string",
            )
        value = candidate.strip()
        if value:
            normalized.append(value)
    if not normalized:
        raise RpcError(
            -32602, "Invalid params: at least one non-empty SMILES is required"
        )
    return normalized


async def _call_tool(name: str, arguments: Dict[str, Any]) -> Dict[str, Any]:
    cid = arguments.get("correlationId")
    token = correlation_id.set(cid) if isinstance(cid, str) and cid else None
    settings = get_settings()

    try:
        async with AdmetClient() as client:
            if name == "wash_molecule":
                smiles_arg = arguments.get("SMILES", arguments.get("smiles"))
                smiles = _coerce_smiles(smiles_arg)
                return await client.wash_molecule(smiles)

            if name == "render_molecule_svg":
                smiles = arguments.get("SMILES", arguments.get("smiles"))
                if not isinstance(smiles, str) or not smiles.strip():
                    raise RpcError(-32602, "Invalid params: SMILES is required")
                figsize = arguments.get("figsize")
                return await client.render_molecule_svg(
                    smiles=smiles.strip(), figsize=figsize
                )

            if name == "predict_admet":
                smiles_arg = arguments.get("SMILES", arguments.get("smiles"))
                smiles_list = _coerce_smiles(smiles_arg)
                feature = arguments.get("feature")
                uncertain = arguments.get("uncertain")
                batches = _chunk_smiles(smiles_list, settings.batch_size)
                results: List[Dict[str, Any]] = []
                for batch in batches:
                    resp = await client.predict_admet(
                        smiles=batch,
                        feature=feature,
                        uncertain=uncertain,
                    )
                    results.append(resp)
                return {"batches": results, "batchCount": len(results)}

            if name == "fetch_admet_csv":
                task_id = arguments.get("taskId") or arguments.get("task_id")
                if not isinstance(task_id, str) or not task_id.strip():
                    raise RpcError(-32602, "Invalid params: taskId is required")
                resp = await client.fetch_admet_csv(task_id=task_id.strip())
                return {
                    "status": resp.status_code,
                    "headers": dict(resp.headers),
                    "content": resp.text,
                }
    except httpx.HTTPError as exc:
        logger.warning("ADMETlab upstream request failed", exc_info=exc)
        raise RpcError(-32002, "Upstream ADMETlab request failed") from exc
    finally:
        if token is not None:
            correlation_id.reset(token)

    raise RpcError(-32601, f"Method not found: unknown tool {name}")


async def _handle_tools_call(params: Dict[str, Any]) -> Dict[str, Any]:
    name = params.get("name")
    arguments = params.get("arguments", {}) or {}
    if not isinstance(name, str) or not name:
        raise RpcError(-32602, "Invalid params: tool name is required")
    if not isinstance(arguments, dict):
        raise RpcError(-32602, "Invalid params: arguments must be an object")
    result = await _call_tool(name=name, arguments=arguments)
    return {
        "content": [
            {
                "type": "text",
                "text": json.dumps(result, ensure_ascii=False, sort_keys=True),
            }
        ],
        "structuredContent": result,
    }


async def _handle_initialized() -> Dict[str, str]:
    return {"status": "ok"}


async def _handle_shutdown() -> Dict[str, str]:
    return {"status": "shutting_down"}


async def _dispatch(method: str, params: Dict[str, Any]) -> Any:
    if method == "initialize":
        return await _handle_initialize(params)
    if method in {"notifications/initialized", "initialized"}:
        return await _handle_initialized()
    if method == "shutdown":
        return await _handle_shutdown()
    if method == "exit":
        return {"status": "exited"}
    if method in {"tools/list", "tools.list"}:
        return await _handle_tools_list()
    if method in {"tools/call", "tools.call"}:
        return await _handle_tools_call(params)
    raise RpcError(-32601, f"Method not found: {method}")


@app.post("/mcp")
async def mcp_endpoint(request: Request) -> Response:
    try:
        payload = await request.json()
    except json.JSONDecodeError:
        return JSONResponse(
            _rpc_error_payload(rpc_id=None, code=-32700, message="Parse error"),
            status_code=400,
        )

    if not isinstance(payload, dict):
        return JSONResponse(
            _rpc_error_payload(rpc_id=None, code=-32600, message="Invalid Request"),
            status_code=400,
        )

    is_notification = "id" not in payload
    rpc_id = payload.get("id")
    method = payload.get("method")
    params = payload.get("params", {})
    if params is None:
        params = {}

    try:
        if not isinstance(method, str) or not method:
            raise RpcError(-32600, "Invalid Request: method must be a non-empty string")
        if not isinstance(params, dict):
            raise RpcError(-32602, "Invalid params: params must be an object")

        result = await _dispatch(method, params)
        if is_notification:
            return Response(status_code=202)
        return JSONResponse({"jsonrpc": "2.0", "id": rpc_id, "result": result})
    except RpcError as exc:
        if is_notification:
            return Response(status_code=400)
        return JSONResponse(
            _rpc_error_payload(
                rpc_id=rpc_id,
                code=exc.code,
                message=exc.message,
                data=exc.data,
            ),
            status_code=exc.status_code,
        )
    except Exception:  # pragma: no cover - fallback
        logger.exception("Unhandled error in MCP endpoint")
        return JSONResponse(
            _rpc_error_payload(rpc_id=rpc_id, code=-32603, message="Internal error"),
            status_code=500,
        )
