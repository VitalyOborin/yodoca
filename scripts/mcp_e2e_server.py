#!/usr/bin/env python3
"""MCP server (stdio) for E2E testing Yodoca via HTTP — wraps AG-UI /agent and /api/health.

Run the application first (`uv run python -m supervisor`). Configure Cursor via `.cursor/mcp.json`.

Environment:
  YODOCA_BASE_URL  — default http://127.0.0.1:8080
  YODOCA_API_KEY   — optional Bearer token (omit if web_channel has no api_key)
  YODOCA_TIMEOUT   — read timeout seconds for agent runs (default 120)
"""

from __future__ import annotations

import asyncio
import json
import os
import uuid
from typing import Any

import httpx
from mcp.server.fastmcp import FastMCP

_BASE_URL = os.environ.get("YODOCA_BASE_URL", "http://127.0.0.1:8080").rstrip("/")
_API_KEY = os.environ.get("YODOCA_API_KEY", "").strip()
_TIMEOUT_SEC = float(os.environ.get("YODOCA_TIMEOUT", "120"))

mcp = FastMCP(
    "yodoca-e2e",
    instructions=(
        "Tools to drive the Yodoca agent over HTTP for E2E testing. "
        "Requires the app running with web_channel (default 127.0.0.1:8080)."
    ),
)


def _auth_headers() -> dict[str, str]:
    h: dict[str, str] = {}
    if _API_KEY:
        h["Authorization"] = f"Bearer {_API_KEY}"
    return h


def _client_timeout() -> httpx.Timeout:
    return httpx.Timeout(_TIMEOUT_SEC, connect=30.0)


async def _parse_ag_ui_sse(response: httpx.Response) -> tuple[str, str | None]:
    """Consume AG-UI SSE from POST /agent; return (assistant_text, run_error_message)."""
    parts: list[str] = []
    run_error: str | None = None

    async for raw_line in response.aiter_lines():
        line = raw_line.strip()
        if not line.startswith("data:"):
            continue
        payload = line[5:].strip()
        if not payload:
            continue
        try:
            obj: dict[str, Any] = json.loads(payload)
        except json.JSONDecodeError:
            continue

        et = obj.get("type")
        if et == "TEXT_MESSAGE_CONTENT":
            parts.append(str(obj.get("delta") or ""))
        elif et == "RUN_FINISHED":
            break
        elif et == "RUN_ERROR":
            msg = obj.get("message")
            code = obj.get("code")
            run_error = f"{msg} (code={code})" if code else str(msg or obj)
            break

    return "".join(parts), run_error


@mcp.tool(
    name="send_message",
    description=(
        "Send a user message to the Yodoca agent via POST /agent (AG-UI). "
        "Streams SSE until the run finishes; returns the full assistant text plus "
        "thread_id and run_id for correlation."
    ),
)
async def send_message(text: str, thread_id: str | None = None) -> str:
    """Send `text` to the agent; optional `thread_id` for conversation continuity."""
    tid = thread_id.strip() if thread_id else f"thread_{uuid.uuid4().hex[:16]}"
    rid = f"run_{uuid.uuid4().hex[:16]}"
    body = {
        "threadId": tid,
        "runId": rid,
        "messages": [{"role": "user", "content": text}],
    }
    headers = {
        **_auth_headers(),
        "Accept": "text/event-stream",
        "Content-Type": "application/json",
        "X-Thread-Id": tid,
    }
    url = f"{_BASE_URL}/agent"

    async with httpx.AsyncClient(timeout=_client_timeout()) as client:
        async with client.stream("POST", url, json=body, headers=headers) as resp:
            ct = (resp.headers.get("content-type") or "").lower()
            if resp.status_code != 200:
                raw = await resp.aread()
                try:
                    err_obj = json.loads(raw.decode("utf-8"))
                except (json.JSONDecodeError, UnicodeDecodeError):
                    err_obj = {"detail": raw.decode("utf-8", errors="replace")[:2000]}
                return json.dumps(
                    {
                        "ok": False,
                        "status": resp.status_code,
                        "thread_id": tid,
                        "run_id": rid,
                        "error": err_obj,
                    },
                    ensure_ascii=False,
                )

            if "text/event-stream" not in ct and "application/x-ndjson" not in ct:
                raw = await resp.aread()
                return json.dumps(
                    {
                        "ok": False,
                        "status": resp.status_code,
                        "thread_id": tid,
                        "run_id": rid,
                        "error": {
                            "message": "Unexpected content-type",
                            "content_type": ct,
                            "body_preview": raw.decode("utf-8", errors="replace")[
                                :2000
                            ],
                        },
                    },
                    ensure_ascii=False,
                )

            assistant_text, run_error = await _parse_ag_ui_sse(resp)

            out: dict[str, Any] = {
                "ok": run_error is None,
                "thread_id": tid,
                "run_id": rid,
                "response": assistant_text,
            }
            if run_error:
                out["error"] = run_error
            return json.dumps(out, ensure_ascii=False)


@mcp.tool(
    name="health_check",
    description="GET /api/health — verify the Yodoca web channel is up.",
)
async def health_check() -> str:
    url = f"{_BASE_URL}/api/health"
    async with httpx.AsyncClient(timeout=httpx.Timeout(15.0, connect=5.0)) as client:
        try:
            r = await client.get(url, headers=_auth_headers())
        except httpx.RequestError as e:
            return json.dumps(
                {"ok": False, "error": str(e), "base_url": _BASE_URL},
                ensure_ascii=False,
            )
        try:
            data = r.json()
        except json.JSONDecodeError:
            data = {"raw": r.text[:2000]}
        return json.dumps(
            {
                "ok": r.is_success,
                "status": r.status_code,
                "body": data,
                "base_url": _BASE_URL,
            },
            ensure_ascii=False,
        )


def main() -> None:
    asyncio.run(mcp.run_stdio_async())


if __name__ == "__main__":
    main()
