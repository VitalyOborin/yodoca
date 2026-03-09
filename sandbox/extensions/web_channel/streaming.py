"""SSE formatters for Chat Completions and Responses API streaming."""

import json
import uuid
from typing import Any


def format_chat_chunk(
    chunk_id: str,
    model: str,
    delta_content: str = "",
    finish_reason: str | None = None,
    created: int | None = None,
) -> str:
    """Format a Chat Completions SSE chunk.

    Args:
        chunk_id: Response ID (e.g. chatcmpl-<uuid>)
        model: Model name
        delta_content: Content delta for this chunk
        finish_reason: "stop" when done, None otherwise
        created: Unix timestamp (optional)
    """
    import time

    ts = created if created is not None else int(time.time())
    delta: dict[str, Any] = {}
    if finish_reason is None:
        if delta_content:
            delta["content"] = delta_content
        else:
            delta["role"] = "assistant"
            delta["content"] = ""
    else:
        delta = {}

    payload = {
        "id": chunk_id,
        "object": "chat.completion.chunk",
        "created": ts,
        "model": model,
        "choices": [
            {
                "index": 0,
                "delta": delta,
                "finish_reason": finish_reason,
            }
        ],
    }
    return f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"


def format_chat_done() -> str:
    """Format the [DONE] sentinel for Chat Completions stream."""
    return "data: [DONE]\n\n"


def format_responses_event(event_type: str, data: dict[str, Any]) -> str:
    """Format a Responses API SSE event.

    Args:
        event_type: e.g. response.created, response.output_text.delta
        data: JSON-serializable event payload
    """
    return f"event: {event_type}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


def build_responses_event_sequence(
    response_id: str,
    model: str,
    msg_id: str,
    full_text: str,
    created: int | None = None,
) -> list[tuple[str, dict[str, Any]]]:
    """Build the full Responses API event sequence for non-streaming.

    Returns list of (event_type, data) for a complete response.
    """
    import time

    ts = created if created is not None else int(time.time())
    return [
        (
            "response.created",
            {
                "id": response_id,
                "object": "response",
                "status": "in_progress",
                "model": model,
                "created": ts,
            },
        ),
        (
            "response.output_item.added",
            {
                "item": {
                    "id": msg_id,
                    "type": "message",
                    "role": "assistant",
                    "content": [],
                }
            },
        ),
        (
            "response.content_part.added",
            {
                "item_id": msg_id,
                "content_index": 0,
                "part": {"type": "output_text", "text": ""},
            },
        ),
        (
            "response.output_text.done",
            {
                "item_id": msg_id,
                "content_index": 0,
                "text": full_text,
            },
        ),
        (
            "response.completed",
            {
                "id": response_id,
                "object": "response",
                "status": "completed",
                "model": model,
                "usage": {
                    "input_tokens": 0,
                    "output_tokens": 0,
                    "total_tokens": 0,
                },
            },
        ),
    ]


def generate_msg_id() -> str:
    """Generate a message ID for Responses API."""
    return f"msg_{uuid.uuid4().hex[:24]}"


def generate_response_id() -> str:
    """Generate a response ID for Responses API."""
    return f"resp_{uuid.uuid4().hex[:24]}"


def generate_chat_id() -> str:
    """Generate a chat completion ID."""
    return f"chatcmpl_{uuid.uuid4().hex[:24]}"
