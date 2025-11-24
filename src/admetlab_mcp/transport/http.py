from __future__ import annotations

import asyncio
import math
from typing import Any, Dict, List, Optional

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse

from ..client.admet_client import AdmetClient
from ..logging import configure_logging, correlation_id
from ..settings import get_settings


app = FastAPI(title="ADMETlab 3.0 MCP Server", version="0.1.0")


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
                "properties": {"SMILES": {"type": ["string", "array"], "items": {"type": "string"}}},
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
                    "figsize": {"type": "array", "items": {"type": "integer"}, "maxItems": 2, "minItems": 2},
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
                    "SMILES": {"type": ["array", "string"], "items": {"type": "string"}},
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


async def _handle_initialize(params: Dict[str, Any]) -> Dict[str, Any]:
    settings = get_settings()
    return {
        "protocolVersion": "2024-11-05",
        "capabilities": {"tools": True},
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


async def _call_tool(name: str, arguments: Dict[str, Any]) -> Dict[str, Any]:
    cid = arguments.pop("correlationId", None)
    if cid:
        correlation_id.set(cid)
    settings = get_settings()
    async with AdmetClient() as client:
        if name == "wash_molecule":
            smiles_arg = arguments.get("SMILES") or arguments.get("smiles")
            if isinstance(smiles_arg, str):
                smiles = [smiles_arg]
            else:
                smiles = list(smiles_arg or [])
            return await client.wash_molecule(smiles)

        if name == "render_molecule_svg":
            smiles = arguments.get("SMILES") or arguments.get("smiles")
            if not smiles:
                raise HTTPException(status_code=400, detail="SMILES is required")
            figsize = arguments.get("figsize")
            return await client.render_molecule_svg(smiles=smiles, figsize=figsize)

        if name == "predict_admet":
            smiles_arg = arguments.get("SMILES") or arguments.get("smiles")
            if isinstance(smiles_arg, str):
                smiles_list = [smiles_arg]
            else:
                smiles_list = list(smiles_arg or [])
            feature = arguments.get("feature")
            uncertain = arguments.get("uncertain")
            batches = _chunk_smiles(smiles_list, settings.batch_size)
            results: List[Dict[str, Any]] = []
            for batch in batches or [[]]:
                resp = await client.predict_admet(
                    smiles=batch,
                    feature=feature,
                    uncertain=uncertain,
                )
                results.append(resp)
            return {"batches": results, "batchCount": len(results)}

        if name == "fetch_admet_csv":
            task_id = arguments.get("taskId") or arguments.get("task_id")
            if not task_id:
                raise HTTPException(status_code=400, detail="taskId is required")
            resp = await client.fetch_admet_csv(task_id=task_id)
            return {
                "status": resp.status_code,
                "headers": dict(resp.headers),
                "content": resp.text,
            }

    raise HTTPException(status_code=404, detail=f"Unknown tool: {name}")


async def _handle_tools_call(params: Dict[str, Any]) -> Dict[str, Any]:
    name = params.get("name")
    arguments = params.get("arguments", {}) or {}
    if not name:
        raise HTTPException(status_code=400, detail="Tool name is required")
    result = await _call_tool(name=name, arguments=arguments)
    return {"content": result}


async def _handle_initialized() -> Dict[str, str]:
    return {"status": "ok"}


async def _handle_shutdown() -> Dict[str, str]:
    return {"status": "shutting_down"}


async def _dispatch(method: str, params: Dict[str, Any]) -> Any:
    if method == "initialize":
        return await _handle_initialize(params)
    if method == "initialized":
        return await _handle_initialized()
    if method == "shutdown":
        return await _handle_shutdown()
    if method == "exit":
        return {"status": "exited"}
    if method in {"tools/list", "tools.list"}:
        return await _handle_tools_list()
    if method in {"tools/call", "tools.call"}:
        return await _handle_tools_call(params)
    raise HTTPException(status_code=404, detail=f"Unknown method {method}")


@app.post("/mcp")
async def mcp_endpoint(request: Request) -> JSONResponse:
    payload = await request.json()
    rpc_id = payload.get("id")
    method = payload.get("method")
    params = payload.get("params", {}) or {}
    try:
        result = await _dispatch(method, params)
        return JSONResponse({"jsonrpc": "2.0", "id": rpc_id, "result": result})
    except HTTPException as exc:
        return JSONResponse(
            {"jsonrpc": "2.0", "id": rpc_id, "error": {"code": exc.status_code, "message": exc.detail}},
            status_code=exc.status_code,
        )
    except Exception as exc:  # pragma: no cover - fallback
        return JSONResponse(
            {"jsonrpc": "2.0", "id": rpc_id, "error": {"code": 500, "message": str(exc)}},
            status_code=500,
        )
