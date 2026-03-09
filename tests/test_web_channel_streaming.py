"""Tests for web_channel SSE streaming formatters."""

import json

from sandbox.extensions.web_channel.streaming import (
    format_chat_chunk,
    format_chat_done,
    format_responses_event,
    generate_chat_id,
    generate_msg_id,
    generate_response_id,
)


class TestStreamingFormatters:
    """SSE format correctness."""

    def test_format_chat_chunk_initial(self) -> None:
        out = format_chat_chunk("chatcmpl-1", "yodoca", "", None)
        assert out.startswith("data: ")
        data = json.loads(out[6:].strip())
        assert data["id"] == "chatcmpl-1"
        assert data["object"] == "chat.completion.chunk"
        assert data["model"] == "yodoca"
        assert len(data["choices"]) == 1
        assert data["choices"][0]["delta"].get("role") == "assistant"
        assert data["choices"][0]["finish_reason"] is None

    def test_format_chat_chunk_content(self) -> None:
        out = format_chat_chunk("chatcmpl-1", "yodoca", "Hello", None)
        data = json.loads(out[6:].strip())
        assert data["choices"][0]["delta"]["content"] == "Hello"

    def test_format_chat_chunk_finish(self) -> None:
        out = format_chat_chunk("chatcmpl-1", "yodoca", "", "stop")
        data = json.loads(out[6:].strip())
        assert data["choices"][0]["delta"] == {}
        assert data["choices"][0]["finish_reason"] == "stop"

    def test_format_chat_done(self) -> None:
        assert format_chat_done() == "data: [DONE]\n\n"

    def test_format_responses_event(self) -> None:
        out = format_responses_event(
            "response.output_text.delta",
            {"item_id": "msg_1", "content_index": 0, "delta": "Hi"},
        )
        assert "event: response.output_text.delta\n" in out
        assert "data: " in out
        data_line = out.split("\n")[1]
        data = json.loads(data_line[6:])
        assert data["delta"] == "Hi"

    def test_generate_ids_format(self) -> None:
        chat_id = generate_chat_id()
        assert chat_id.startswith("chatcmpl_")
        assert len(chat_id) > 10
        msg_id = generate_msg_id()
        assert msg_id.startswith("msg_")
        resp_id = generate_response_id()
        assert resp_id.startswith("resp_")
