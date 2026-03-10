#!/usr/bin/env python3
"""Smoke test script for Yodoca Web Channel API endpoints.

Runs against a live server and validates all endpoints from
docs/api/web-channel-openapi.yaml.

Usage: uv run python scripts/check_api.py [--base-url URL] [--api-key KEY]
"""

import argparse
import sys
from typing import Any

import httpx


def _headers(api_key: str | None) -> dict[str, str]:
    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    return headers


def _check(name: str, passed: bool, detail: str = "") -> bool:
    status = "PASS" if passed else "FAIL"
    msg = f"  [{status}] {name}"
    if detail:
        msg += f" — {detail}"
    print(msg)
    return passed


def _validate_models(data: dict[str, Any]) -> str | None:
    if "object" not in data or data["object"] != "list":
        return "expected object=list"
    if "data" not in data or not isinstance(data["data"], list):
        return "expected data array"
    for item in data["data"]:
        for key in ("id", "object", "created", "owned_by"):
            if key not in item:
                return f"missing {key} in model item"
    return None


def _validate_health(data: dict[str, Any]) -> str | None:
    if data.get("status") != "ok":
        return "expected status=ok"
    if "uptime_seconds" not in data:
        return "missing uptime_seconds"
    return None


def _validate_session(data: dict[str, Any]) -> str | None:
    for key in ("id", "channel_id", "created_at", "last_active_at", "is_archived"):
        if key not in data:
            return f"missing {key} in session"
    return None


def _validate_project(data: dict[str, Any]) -> str | None:
    for key in ("id", "name", "agent_config", "created_at", "updated_at", "files"):
        if key not in data:
            return f"missing {key} in project"
    if not isinstance(data.get("files"), list):
        return "files must be array"
    return None


def _validate_chat_completion(data: dict[str, Any]) -> str | None:
    for key in ("id", "object", "created", "model", "choices", "usage"):
        if key not in data:
            return f"missing {key} in chat completion"
    if data.get("object") != "chat.completion":
        return "expected object=chat.completion"
    return None


def _validate_responses(data: dict[str, Any]) -> str | None:
    for key in ("id", "object", "status", "output", "model", "usage"):
        if key not in data:
            return f"missing {key} in responses"
    if data.get("object") != "response":
        return "expected object=response"
    return None


def _validate_error(data: dict[str, Any]) -> str | None:
    if "error" not in data:
        return "missing error"
    err = data["error"]
    if "message" not in err or "type" not in err:
        return "error must have message and type"
    return None


def run_checks(
    base_url: str,
    api_key: str | None,
    timeout: float,
) -> tuple[int, int]:
    passed_count = 0
    failed_count = 0
    client = httpx.Client(
        base_url=base_url.rstrip("/"),
        headers=_headers(api_key),
        timeout=timeout,
    )

    session_id: str | None = None
    project_id: str | None = None

    try:
        # --- System ---
        resp = client.get("/v1/models")
        err = _validate_models(resp.json()) if resp.status_code == 200 else resp.text
        ok = resp.status_code == 200 and err is None
        if _check("GET /v1/models", ok, err or str(resp.status_code)):
            passed_count += 1
        else:
            failed_count += 1

        resp = client.get("/api/health")
        err = _validate_health(resp.json()) if resp.status_code == 200 else resp.text
        ok = resp.status_code == 200 and err is None
        if _check("GET /api/health", ok, err or str(resp.status_code)):
            passed_count += 1
        else:
            failed_count += 1

        # --- Sessions CRUD ---
        resp = client.post("/api/sessions", json={"title": "Smoke test session"})
        if resp.status_code == 200:
            data = resp.json()
            session = data.get("session")
            err = _validate_session(session) if session else "missing session"
            if err is None:
                session_id = session["id"]
        else:
            err = resp.text
        ok = resp.status_code == 200 and err is None
        if _check("POST /api/sessions", ok, err or str(resp.status_code)):
            passed_count += 1
        else:
            failed_count += 1

        resp = client.get("/api/sessions")
        if resp.status_code == 200:
            data = resp.json()
            sessions = data.get("sessions", [])
            err = None
            if session_id and not any(s.get("id") == session_id for s in sessions):
                err = f"created session {session_id} not in list"
            elif not isinstance(sessions, list):
                err = "sessions must be array"
        else:
            err = resp.text
        ok = resp.status_code == 200 and err is None
        if _check("GET /api/sessions", ok, err or str(resp.status_code)):
            passed_count += 1
        else:
            failed_count += 1

        if session_id:
            resp = client.get(f"/api/sessions/{session_id}")
            if resp.status_code == 200:
                data = resp.json()
                sess = data.get("session")
                hist = data.get("history")
                err = _validate_session(sess) if sess else "missing session"
                if err is None and hist is None:
                    err = "missing history"
                elif err is None and not isinstance(hist, list):
                    err = "history must be array"
            else:
                err = resp.text
            ok = resp.status_code == 200 and err is None
            detail = err or str(resp.status_code)
            if _check(f"GET /api/sessions/{session_id}", ok, detail):
                passed_count += 1
            else:
                failed_count += 1

            resp = client.patch(
                f"/api/sessions/{session_id}",
                json={"title": "Renamed smoke session"},
            )
            if resp.status_code == 200:
                data = resp.json()
                sess = data.get("session")
                err = _validate_session(sess) if sess else "missing session"
                if err is None and sess.get("title") != "Renamed smoke session":
                    err = "title not updated"
            else:
                err = resp.text
            ok = resp.status_code == 200 and err is None
            detail = err or str(resp.status_code)
            if _check(f"PATCH /api/sessions/{session_id}", ok, detail):
                passed_count += 1
            else:
                failed_count += 1

            resp = client.delete(f"/api/sessions/{session_id}")
            if resp.status_code == 200:
                data = resp.json()
                err = None if data.get("success") is True else "expected success=true"
            else:
                err = resp.text
            ok = resp.status_code == 200 and err is None
            detail = err or str(resp.status_code)
            if _check(f"DELETE /api/sessions/{session_id}", ok, detail):
                passed_count += 1
            else:
                failed_count += 1

        # --- Projects CRUD ---
        resp = client.post(
            "/api/projects",
            json={"name": "Smoke test project", "agent_config": {}, "files": []},
        )
        if resp.status_code == 200:
            data = resp.json()
            proj = data.get("project")
            err = _validate_project(proj) if proj else "missing project"
            if err is None:
                project_id = proj["id"]
        else:
            err = resp.text
        ok = resp.status_code == 200 and err is None
        if _check("POST /api/projects", ok, err or str(resp.status_code)):
            passed_count += 1
        else:
            failed_count += 1

        resp = client.get("/api/projects")
        if resp.status_code == 200:
            data = resp.json()
            projects = data.get("projects", [])
            err = None
            if project_id and not any(p.get("id") == project_id for p in projects):
                err = f"created project {project_id} not in list"
            elif not isinstance(projects, list):
                err = "projects must be array"
        else:
            err = resp.text
        ok = resp.status_code == 200 and err is None
        if _check("GET /api/projects", ok, err or str(resp.status_code)):
            passed_count += 1
        else:
            failed_count += 1

        if project_id:
            resp = client.get(f"/api/projects/{project_id}")
            if resp.status_code == 200:
                data = resp.json()
                proj = data.get("project")
                err = _validate_project(proj) if proj else "missing project"
            else:
                err = resp.text
            ok = resp.status_code == 200 and err is None
            detail = err or str(resp.status_code)
            if _check(f"GET /api/projects/{project_id}", ok, detail):
                passed_count += 1
            else:
                failed_count += 1

            resp = client.patch(
                f"/api/projects/{project_id}",
                json={"name": "Renamed smoke project"},
            )
            if resp.status_code == 200:
                data = resp.json()
                proj = data.get("project")
                err = _validate_project(proj) if proj else "missing project"
                if err is None and proj.get("name") != "Renamed smoke project":
                    err = "name not updated"
            else:
                err = resp.text
            ok = resp.status_code == 200 and err is None
            detail = err or str(resp.status_code)
            if _check(f"PATCH /api/projects/{project_id}", ok, detail):
                passed_count += 1
            else:
                failed_count += 1

            resp = client.delete(f"/api/projects/{project_id}")
            if resp.status_code == 200:
                data = resp.json()
                err = None if data.get("success") is True else "expected success=true"
            else:
                err = resp.text
            ok = resp.status_code == 200 and err is None
            detail = err or str(resp.status_code)
            if _check(f"DELETE /api/projects/{project_id}", ok, detail):
                passed_count += 1
            else:
                failed_count += 1

        # --- Notifications ---
        resp = client.get("/api/notifications?timeout=1")
        if resp.status_code == 200:
            data = resp.json()
            notifs = data.get("notifications")
            has_notifs = isinstance(notifs, list)
            err = None if has_notifs else "missing notifications array"
        else:
            err = resp.text
        ok = resp.status_code == 200 and err is None
        if _check("GET /api/notifications", ok, err or str(resp.status_code)):
            passed_count += 1
        else:
            failed_count += 1

        # --- OpenAI-compatible ---
        resp = None
        err = None
        ok = False
        try:
            resp = client.post(
                "/v1/chat/completions",
                json={
                    "model": "yodoca",
                    "messages": [{"role": "user", "content": "Reply with exactly: OK"}],
                    "stream": False,
                },
            )
            if resp.status_code == 200:
                err = _validate_chat_completion(resp.json())
            elif resp.status_code == 503:
                chat_data = resp.json()
                err = _validate_error(chat_data) if chat_data else "empty response"
                if err is None and chat_data.get("error", {}).get("code") != "busy":
                    err = "expected busy error"
            else:
                err = resp.text
            ok = (resp.status_code == 200 and err is None) or (
                resp.status_code == 503 and err is None
            )
        except httpx.RequestError as e:
            ok = False
            err = str(e)
        detail = err or (str(resp.status_code) if resp else "")
        if _check("POST /v1/chat/completions", ok, detail):
            passed_count += 1
        else:
            failed_count += 1

        resp = None
        err = None
        ok = False
        try:
            resp = client.post(
                "/v1/responses",
                json={
                    "model": "yodoca",
                    "input": "Reply with exactly: OK",
                    "stream": False,
                },
            )
            if resp.status_code == 200:
                err = _validate_responses(resp.json())
            elif resp.status_code == 503:
                resp_data = resp.json()
                err = _validate_error(resp_data) if resp_data else "empty response"
                if err is None and resp_data.get("error", {}).get("code") != "busy":
                    err = "expected busy error"
            else:
                err = resp.text
            ok = (resp.status_code == 200 and err is None) or (
                resp.status_code == 503 and err is None
            )
        except httpx.RequestError as e:
            ok = False
            err = str(e)
        detail = err or (str(resp.status_code) if resp else "")
        if _check("POST /v1/responses", ok, detail):
            passed_count += 1
        else:
            failed_count += 1

        # --- Error cases ---
        resp = client.get("/api/sessions/nonexistent")
        err = _validate_error(resp.json()) if resp.status_code == 404 else resp.text
        ok = resp.status_code == 404 and err is None
        detail = err or str(resp.status_code)
        if _check("GET /api/sessions/nonexistent -> 404", ok, detail):
            passed_count += 1
        else:
            failed_count += 1

        resp = client.get("/api/projects/nonexistent")
        err = _validate_error(resp.json()) if resp.status_code == 404 else resp.text
        ok = resp.status_code == 404 and err is None
        detail = err or str(resp.status_code)
        if _check("GET /api/projects/nonexistent -> 404", ok, detail):
            passed_count += 1
        else:
            failed_count += 1

        resp = client.post(
            "/v1/chat/completions",
            json={
                "model": "yodoca",
                "messages": [{"role": "assistant", "content": "hi"}],
            },
        )
        err = _validate_error(resp.json()) if resp.status_code == 422 else resp.text
        ok = resp.status_code == 422 and err is None
        if _check(
            "POST /v1/chat/completions (no user msg) -> 422",
            ok,
            err or str(resp.status_code),
        ):
            passed_count += 1
        else:
            failed_count += 1

    finally:
        client.close()

    return passed_count, failed_count


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Smoke test web channel API endpoints from OpenAPI spec."
    )
    parser.add_argument(
        "--base-url",
        default="http://127.0.0.1:8080",
        help="Base URL of the API server (default: http://127.0.0.1:8080)",
    )
    parser.add_argument(
        "--api-key",
        default=None,
        help="Bearer token (optional if api_key not configured)",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=120.0,
        help="Request timeout in seconds (default: 120 for LLM calls)",
    )
    args = parser.parse_args()

    print(f"Checking API at {args.base_url}...")
    passed, failed = run_checks(args.base_url, args.api_key, args.timeout)
    total = passed + failed
    print(f"\nSummary: {passed}/{total} passed, {failed} failed")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
