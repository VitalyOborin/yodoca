# ADR 010: Streaming Response Delivery via Dual-Path Agent Invocation

## Status

Proposed.

## Context

Every user-facing response today follows a request-response cycle:

```
Channel (user.message event)
  → EventBus → kernel_user_message_handler
    → MessageRouter.handle_user_message()
      → invoke_agent() → Runner.run() → final_output (str)
    → channel.send_to_user(user_id, complete_response)
```

The user sees nothing until the agent finishes — including all tool calls, handoffs, and LLM generation. For responses that take 5–30 seconds (memory retrieval + reasoning + multiple tool calls), this creates a "dead air" effect: no feedback, no progress indication, no partial output.

Three structural problems follow from this:

1. **No incremental delivery.** `ChannelProvider.send_to_user()` accepts a complete `str`. There is no mechanism to push partial text or status updates to the user while the agent is working. CLI users stare at a blank prompt; Telegram users see no typing indicator or intermediate text.
2. **No channel-level opt-in.** Streaming capability depends on the transport: CLI can print token-by-token trivially; Telegram can edit messages but has rate limits (~1 edit/sec per chat); a future Web GUI could use SSE or WebSocket. The kernel has no way to know whether a channel can handle incremental delivery, so it cannot branch behavior.
3. **SDK support is available but unused.** The OpenAI Agents SDK provides `Runner.run_streamed()` which returns a `RunResultStreaming` with `stream_events()` — an async iterator yielding `RawResponsesStreamEvent` (token deltas), `RunItemStreamEvent` (tool calls, messages, handoffs), and `AgentUpdatedStreamEvent` (agent changes). The API accepts the same parameters as `Runner.run()` including `session=`. The capability exists; the kernel simply doesn't use it.

### Design constraints

- **Backward compatibility is mandatory.** Channels that do not implement streaming must continue to work exactly as before with zero code changes.
- **Channels must remain SDK-agnostic.** A channel extension is a transport adapter. It should not import `agents.stream_events` or understand `RunResultStreaming`. The kernel translates SDK events into a simple channel-facing interface.
- **Serialization semantics are preserved.** The current `asyncio.Lock` in `MessageRouter` prevents interleaved agent invocations. Streaming holds the lock for the entire stream duration — same guarantee, longer hold time.
- **Event contract is preserved.** Subscribers to `user_message` and `agent_response` internal events receive the same payloads as today. Streaming is a transport concern between kernel and channel, not an event-bus concern.

## Decision

### 1. Core Principle: Protocol Detection Drives Behavior

The kernel detects streaming capability via a new `StreamingChannelProvider` protocol, following the same `isinstance()` detection pattern used for `ToolProvider`, `ServiceProvider`, `ContextProvider`, and all other extension capabilities. No manifest field, no configuration flag — the protocol *is* the declaration.

Channels that implement `StreamingChannelProvider` get token-by-token delivery. Channels that don't implement it get the complete response as before. The kernel branches at the `handle_user_message()` call site.

### 2. New Protocol: `StreamingChannelProvider`

Added to `core/extensions/contract.py` alongside existing protocols:

```python
@runtime_checkable
class StreamingChannelProvider(Protocol):
    """Channel that supports incremental response delivery.

    Implement alongside ChannelProvider to receive token-by-token output.
    Channels that do not implement this protocol receive complete responses
    via send_to_user() as before.
    """

    async def on_stream_start(self, user_id: str) -> None:
        """Called when agent starts generating a response.
        Use for typing indicators, placeholder messages, etc."""

    async def on_stream_chunk(self, user_id: str, chunk: str) -> None:
        """Deliver an incremental text chunk (typically a few tokens).
        Called many times during a single response."""

    async def on_stream_status(self, user_id: str, status: str) -> None:
        """Inform the user about agent activity (e.g., 'Using tool: search_memory').
        Called when the agent invokes a tool or performs a handoff."""

    async def on_stream_end(self, user_id: str, full_text: str) -> None:
        """Called when the response is complete.
        full_text contains the entire assembled response.
        Use for final message replacement, cleanup, etc."""
```

The four-method lifecycle (`start → chunk* → status* → end`) gives channels full control over presentation:
- CLI: `on_stream_start` is a no-op; `on_stream_chunk` prints without newline; `on_stream_end` prints a trailing newline.
- Telegram: `on_stream_start` sends an initial "..." message and stores its `message_id`; `on_stream_chunk` accumulates in a buffer and edits the message on a timer (e.g., every 500ms); `on_stream_end` performs a final edit with the complete text.
- Web GUI (future): `on_stream_start` creates a response bubble; `on_stream_chunk` appends via WebSocket/SSE; `on_stream_end` finalizes the message.

### 3. Dual-Path Invocation in `MessageRouter`

`MessageRouter` gains a streaming counterpart to `invoke_agent()`:

```python
async def invoke_agent_streamed(
    self, prompt: str, agent_id: str | None = None
) -> RunResultStreaming:
    """Run agent with streaming. Returns RunResultStreaming for event iteration.
    Caller must iterate stream_events() to completion."""
    if not self._agent:
        raise RuntimeError("No agent configured")
    if self._invoke_middleware:
        prompt = await self._invoke_middleware(prompt.strip(), agent_id)
    from agents import Runner
    return Runner.run_streamed(
        self._agent,
        prompt,
        session=self._session,
    )
```

`handle_user_message()` branches based on channel capability:

```python
async def handle_user_message(self, text, user_id, channel):
    # Session rotation (unchanged)
    now = time.time()
    if self._last_message_at and (now - self._last_message_at) > self._session_timeout:
        await self._rotate_session()
    self._last_message_at = now

    await self._emit("user_message", {...})

    if isinstance(channel, StreamingChannelProvider):
        await self._handle_streamed(text, user_id, channel)
    else:
        response = await self.invoke_agent(text)
        await self._emit("agent_response", {...})
        await channel.send_to_user(user_id, response)
```

The streaming path is extracted into a private method:

```python
async def _handle_streamed(self, text, user_id, channel):
    async with self._lock:
        result = await self.invoke_agent_streamed(text)
        await channel.on_stream_start(user_id)
        full_text = ""
        async for event in result.stream_events():
            if (event.type == "raw_response_event"
                    and isinstance(event.data, ResponseTextDeltaEvent)):
                full_text += event.data.delta
                await channel.on_stream_chunk(user_id, event.data.delta)
            elif event.type == "run_item_stream_event":
                if event.item.type == "tool_call_item":
                    tool_name = getattr(event.item, "name", "tool")
                    await channel.on_stream_status(user_id, f"Using: {tool_name}")
        final = result.final_output or full_text
        await channel.on_stream_end(user_id, final)
    await self._emit("agent_response", {"text": final, ...})
```

The lock wraps the entire streaming loop, preserving serialization. The `agent_response` event is emitted after streaming completes, carrying the full text — no change for event subscribers.

### 4. Loader: No Changes Required

The Loader already detects protocols via `isinstance()` in `detect_and_wire_all()`. `StreamingChannelProvider` is detected automatically when `MessageRouter` checks `isinstance(channel, StreamingChannelProvider)` at message-handling time. No separate registration is needed — the channel is already registered as a `ChannelProvider`.

### 5. ExtensionContext: Streaming Access for Extensions

`ExtensionContext` gains an optional streaming method for extensions that need it (e.g., a future Web GUI extension that directly calls the orchestrator):

```python
async def invoke_agent_streamed(self, prompt: str) -> "RunResultStreaming":
    """Ask the agent to process a prompt with streaming. Caller iterates events."""
    return await self._router.invoke_agent_streamed(prompt)
```

This is an opt-in API. Existing extensions that call `invoke_agent()` are unaffected.

### 6. Manifest: Optional Streaming Configuration

Streaming behavior is controlled by protocol implementation, not by manifest. However, channels may need transport-specific tuning. The existing `config:` block in `manifest.yaml` is sufficient:

```yaml
# telegram_channel/manifest.yaml
config:
  polling_timeout: 30
  stream_edit_interval_ms: 500    # how often to edit the Telegram message
  stream_min_chunk_chars: 20      # minimum characters before an edit
```

No schema changes to `ExtensionManifest`. The `config:` dict is already a free-form `dict` that channels read via `context.get_config()`.

### 7. Channel Implementations

#### CLI Channel

Minimal change — implement the four streaming methods:

```python
class CliChannelExtension:
    # ... existing ChannelProvider methods unchanged ...

    # StreamingChannelProvider
    async def on_stream_start(self, _user_id: str) -> None:
        pass  # no indicator needed for CLI

    async def on_stream_chunk(self, _user_id: str, chunk: str) -> None:
        print(chunk, end="", flush=True)

    async def on_stream_status(self, _user_id: str, status: str) -> None:
        print(f"\n  [{status}]", flush=True)

    async def on_stream_end(self, _user_id: str, _full_text: str) -> None:
        print()  # trailing newline
        print()
```

#### Telegram Channel

More complex due to rate limits. Strategy: send an initial message, accumulate chunks in a buffer, edit the message on a timer or when the buffer exceeds a threshold.

```python
class TelegramChannelExtension:
    # ... existing code ...
    _stream_msg_id: int | None = None
    _stream_buffer: str = ""

    async def on_stream_start(self, user_id: str) -> None:
        msg = await self._bot.send_message(chat_id=self._chat_id, text="...")
        self._stream_msg_id = msg.message_id
        self._stream_buffer = ""

    async def on_stream_chunk(self, user_id: str, chunk: str) -> None:
        self._stream_buffer += chunk
        # Edit on timer/threshold (implementation detail)

    async def on_stream_status(self, user_id: str, status: str) -> None:
        # Optionally append status to buffer or send as chat action
        pass

    async def on_stream_end(self, user_id: str, full_text: str) -> None:
        await self._bot.edit_message_text(
            chat_id=self._chat_id,
            message_id=self._stream_msg_id,
            text=full_text,
        )
        self._stream_msg_id = None
        self._stream_buffer = ""
```

The edit debouncing strategy (timer-based, character-threshold, or hybrid) is an internal channel concern. The kernel is unaware of it.

### 8. Event System: No Changes

| Event | Payload | When emitted | Change |
|---|---|---|---|
| `user_message` | `{text, user_id, channel_id}` | Before agent invocation | None |
| `agent_response` | `{text, user_id, channel, session_id, agent_id}` | After complete response | None — emitted after streaming finishes |

Streaming is a transport optimization between kernel and channel. The event bus carries the complete response for all subscribers (memory consolidation, logging, analytics, etc.). No `agent_response_chunk` event is introduced in v1 — it would add complexity without clear consumers.

### 9. Error Handling

Errors during streaming require special handling because partial output may have already been delivered:

- **LLM error mid-stream**: The kernel catches the exception after `stream_events()` terminates. It calls `on_stream_end(user_id, partial_text + "\n\n(Error: ...)")` so the channel can show what was generated plus the error.
- **Channel error** (e.g., Telegram edit fails): Handled internally by the channel. The kernel's streaming loop continues; the channel may degrade to buffering and delivering the final message via `send_to_user()` as a fallback.
- **MaxTurnsExceeded / GuardrailTripwireTriggered**: Raised after `stream_events()` completes. The kernel catches them and reports via `on_stream_end()`.

### 10. Implementation Order

| Step | Scope | Description |
|---|---|---|
| 1 | `core/extensions/contract.py` | Add `StreamingChannelProvider` protocol |
| 2 | `core/extensions/router.py` | Add `invoke_agent_streamed()` and `_handle_streamed()`, branch in `handle_user_message()` |
| 3 | `sandbox/extensions/cli_channel/main.py` | Implement `StreamingChannelProvider` (4 methods) |
| 4 | End-to-end test | Verify CLI streaming works, non-streaming channels unaffected |
| 5 | `sandbox/extensions/telegram_channel/main.py` | Implement `StreamingChannelProvider` with edit-debounce |
| 6 | `core/extensions/context.py` | Add `invoke_agent_streamed()` for extension access |

## Consequences

- **Users see responses as they are generated.** CLI prints token-by-token. Telegram shows a progressively updating message. The "dead air" problem is eliminated for streaming-capable channels.
- **Non-streaming channels require zero changes.** The `ChannelProvider` protocol and `send_to_user()` contract are untouched. Existing extensions continue to work identically.
- **Channels control their own presentation.** The four-method lifecycle gives each transport full control over how chunks are buffered, debounced, and displayed. The kernel does not impose a delivery strategy.
- **Lock hold time increases for streaming.** The `asyncio.Lock` is held for the entire stream duration (seconds, not milliseconds). This is acceptable because agent invocations are already serialized — the lock prevents concurrent calls, not concurrent I/O. If concurrent agent invocations become a requirement in the future, the lock strategy must be revisited independently of streaming.
- **SDK dependency is contained.** Only `MessageRouter._handle_streamed()` imports `ResponseTextDeltaEvent` from the OpenAI SDK. Channel extensions never see SDK types — they receive plain `str` chunks.
- **No new external dependencies.** `Runner.run_streamed()` is part of the already-used `openai-agents` SDK. No additional packages are required.
- **Event contract is stable.** All existing event subscribers (memory, logging, etc.) continue to receive the complete `agent_response` after streaming finishes. No partial-event mechanism is introduced.
- **Future extensibility.** The `on_stream_status()` method can be extended to carry structured data (tool name, progress percentage) when needed. The `StreamingChannelProvider` protocol can gain additional lifecycle methods (e.g., `on_stream_cancel()`) without breaking existing implementations.
