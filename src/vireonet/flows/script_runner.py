"""Separate script-runner service for flow Python scripts (production).

Run it in its own container with NO VireoNet secrets, a read-only root
filesystem, a tmpfs for /tmp, an internal-only network, as a non-root user:

    python -m vireonet.flows.script_runner      # listens on :8100

The worker sends ``POST /run`` with the script and inputs, signed with
HMAC-SHA256 (header X-VireoNet-Signature) using
VIREONET_FLOW_SCRIPT_RUNNER_SECRET; unsigned or badly signed requests get
401. The same sandbox layers as local mode apply inside the runner.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
from typing import Any

from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route

from vireonet.flows.sandbox import DEFAULT_ALLOWED_IMPORTS, ScriptError, run_script

MAX_BODY = 512_000


def sign(secret: str, body: bytes) -> str:
    return hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


def create_runner_app(secret: str | None = None) -> Starlette:
    key = secret if secret is not None else os.environ.get("VIREONET_FLOW_SCRIPT_RUNNER_SECRET", "")
    max_seconds = int(os.environ.get("VIREONET_FLOW_SCRIPT_MAX_SECONDS", "300"))

    async def run(request: Request) -> JSONResponse:
        body = await request.body()
        if not key:
            return JSONResponse(
                {"error": "runner has no VIREONET_FLOW_SCRIPT_RUNNER_SECRET"}, status_code=503
            )
        if len(body) > MAX_BODY:
            return JSONResponse({"error": "request too large"}, status_code=413)
        if not hmac.compare_digest(
            request.headers.get("X-VireoNet-Signature", ""), sign(key, body)
        ):
            return JSONResponse({"error": "bad signature"}, status_code=401)
        req: dict[str, Any] = json.loads(body)
        try:
            out = await run_script(
                str(req.get("code", "")),
                dict(req.get("inputs") or {}),
                timeout=min(int(req.get("timeout") or 30), max_seconds),
                memory_mb=int(req.get("memory_mb") or 512),
                allowed_imports=list(req.get("allowed_imports") or DEFAULT_ALLOWED_IMPORTS),
                require_isolation=bool(req.get("require_isolation", False)),
            )
        except ScriptError as exc:
            return JSONResponse({"error": str(exc)}, status_code=422)
        return JSONResponse({**out, "runner": "sidecar"})

    async def health(_request: Request) -> JSONResponse:
        return JSONResponse({"status": "ok", "configured": bool(key)})

    return Starlette(routes=[Route("/run", run, methods=["POST"]), Route("/healthz", health)])


if __name__ == "__main__":  # pragma: no cover
    import uvicorn

    uvicorn.run(create_runner_app(), host="0.0.0.0", port=int(os.environ.get("PORT", "8100")))
