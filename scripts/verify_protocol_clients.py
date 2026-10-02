"""Run real legacy/modern clients against both transports, including installed wheels."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from urllib.request import urlopen

ROOT = Path(__file__).resolve().parents[1]
TOOL_METADATA_KEY = "org.toxmcp/toolMetadata"


def catalog_fingerprint(items):
    normalized = []
    for tool in items:
        tool = dict(tool)
        metadata = tool.pop("_meta", {})
        if metadata:
            assert set(metadata) == {TOOL_METADATA_KEY}, metadata
            tool["metadata"] = metadata[TOOL_METADATA_KEY]
        normalized.append(tool)
    return hashlib.sha256(
        json.dumps(normalized, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def application_results(report):
    results = {}
    for label, result in report["results"].items():
        result = dict(result)
        metadata = dict(result["_meta"])
        server_info = metadata.pop("io.modelcontextprotocol/serverInfo", None)
        if server_info is not None:
            assert server_info == {
                "name": "admetlab-mcp",
                "version": report.get("serverVersion", "0.1.3"),
            }
        result["_meta"] = metadata
        if "resultType" in result:
            assert result.pop("resultType") == "complete"
        # SDK2 serializes the optional error flag explicitly; SDK1 leaves it unset.
        if "isError" in result:
            flag = result.pop("isError")
            if flag:
                result["isError"] = True
        results[label] = result
    return results


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--legacy-python", required=True)
    parser.add_argument("--server-python", default=sys.executable)
    parser.add_argument("--server-root")
    parser.add_argument("--output-dir", required=True)
    args = parser.parse_args()
    output = Path(args.output_dir).resolve()
    output.mkdir(parents=True, exist_ok=True)
    expected = json.loads(
        (ROOT / "tests/compatibility/v0.1.3-catalog-sha256.json").read_text()
    )
    env = os.environ.copy()
    env.pop("VIRTUAL_ENV", None)
    env.pop("PYTHONPATH", None)
    if args.server_root:
        env["PYTHONPATH"] = str(Path(args.server_root).resolve())
    common = [
        "--server-python",
        args.server_python,
        "--launcher",
        str(ROOT / "scripts/compatibility_server.py"),
        "--fixtures",
        str(ROOT / "tests/compatibility/requests.json"),
    ]
    if args.server_root:
        common += ["--server-root", str(Path(args.server_root).resolve())]
    reports = []
    upstream_requests = []
    with tempfile.TemporaryDirectory(prefix="admetlab-clients-") as workspace:
        for modern in [False, True]:
            python = args.server_python if modern else args.legacy_python
            for transport in ["stdio", "http"]:
                label = ("modern" if modern else "legacy") + "-" + transport
                command = [
                    python,
                    str(ROOT / "scripts/protocol_probe.py"),
                    *common,
                    "--output",
                    str(output / (label + ".json")),
                ]
                if modern:
                    command.append("--modern")
                request_log = output / (label + "-upstream.jsonl")
                request_log.write_text("")
                env["ADMETLAB_COMPATIBILITY_REQUEST_LOG"] = str(request_log)
                command += ["--request-log", str(request_log)]
                server = None
                with (output / (label + ".log")).open("w") as log:
                    try:
                        if transport == "http":
                            with socket.socket() as listener:
                                listener.bind(("127.0.0.1", 0))
                                port = listener.getsockname()[1]
                            server = subprocess.Popen(
                                [
                                    args.server_python,
                                    str(ROOT / "scripts/compatibility_server.py"),
                                    "--transport",
                                    "http",
                                    "--port",
                                    str(port),
                                ],
                                cwd=workspace,
                                env=env,
                                stdout=log,
                                stderr=log,
                            )
                            for _ in range(100):
                                if server.poll() is not None:
                                    raise RuntimeError(f"{label}: server exited")
                                try:
                                    with urlopen(
                                        f"http://127.0.0.1:{port}/health", timeout=0.2
                                    ) as response:
                                        if response.status == 200:
                                            break
                                except OSError:
                                    time.sleep(0.05)
                            else:
                                raise RuntimeError(f"{label}: server did not start")
                            command += ["--url", f"http://127.0.0.1:{port}/mcp"]
                        subprocess.run(
                            command,
                            cwd=workspace,
                            env=env,
                            stdout=log,
                            stderr=log,
                            check=True,
                            timeout=60,
                        )
                    finally:
                        if server:
                            server.terminate()
                            server.wait(timeout=10)
                report = json.loads((output / (label + ".json")).read_text())
                assert len(report["catalog"]) == expected["tools"]
                assert (
                    catalog_fingerprint(report["catalog"]) == expected["sha256"]
                ), label
                records = [
                    json.loads(line) for line in request_log.read_text().splitlines()
                ]
                assert len(records) == 9, (label, records)
                upstream_requests.append(records)
                reports.append(report)
                print(
                    label,
                    report["protocol"],
                    len(report["results"]),
                    "workflows passed",
                    flush=True,
                )
    results = application_results(reports[0])
    for report in reports[1:]:
        assert application_results(report) == results, report["protocol"]
    assert all(records == upstream_requests[0] for records in upstream_requests)
    (output / "summary.json").write_text(
        json.dumps(
            {
                "tools": expected["tools"],
                "catalogSHA256": expected["sha256"],
                "workflows": len(results),
                "combinations": 4,
                "applicationResultsIdentical": True,
                "upstreamRequestsIdentical": True,
                "upstreamRequestsPerCombination": 9,
            },
            indent=2,
        )
        + "\n"
    )


if __name__ == "__main__":
    main()
