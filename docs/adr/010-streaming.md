# ADR 010: Streaming Response Delivery via Dual-Path Agent Invocation

## Status

Accepted.

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

Channels that implement `StreamingChannelProvider` get incremental delivery. Channels that don't implement it get the complete response as before. The kernel branches at the `handle_user_message()` call site.

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
- Telegram: `on_stream_start` sends an initial placeholder message and stores its `message_id`; `on_stream_chunk` accumulates in a buffer and edits the message on a debounce timer (e.g., every 500ms); `on_stream_status` sends a `sendChatAction("typing")` to show a typing indicator while tools execute; `on_stream_end` performs a final edit with the complete text.
- Web GUI (future): `on_stream_start` creates a response bubble; `on_stream_chunk` appends via WebSocket/SSE; `on_stream_end` finalizes the message.

### 3. Callback-Based Streaming Invocation in `MessageRouter`

#### Why callbacks, not an async iterator

A natural API would be `invoke_agent_streamed() -> AsyncIterator[StreamEvent]`. However, this creates a lock-safety problem: the `asyncio.Lock` must be held for the entire duration of the stream, but a generator returned to the caller does not start executing until the first iteration. This creates a window between the method call and the first `yield` where the lock is not yet acquired — or requires the caller to correctly wrap the iteration in `async with self._lock`, splitting lock management across two methods.

The callback approach eliminates this class of bugs: `invoke_agent_streamed()` acquires the lock, iterates the stream internally, calls the provided callbacks for each event, and releases the lock on return. The lock lifecycle is entirely contained within a single `async def`.

#### API

`MessageRouter` gains a streaming counterpart to `invoke_agent()`:

```python
async def invoke_agent_streamed(
    self,
    prompt: str,
    on_chunk: Callable[[str], Awaitable[None]],
    on_tool_call: Callable[[str], Awaitable[None]] | None = None,
    agent_id: str | None = None,
) -> str:
    """Run agent with streaming via callbacks. Returns final_output after completion.

    Lock is acquired internally and held for the entire stream duration.
    Caller never needs to manage the lock.

    Args:
        prompt: User prompt (middleware is applied internally).
        on_chunk: Called with each text delta as it arrives.
        on_tool_call: Called with the tool name when a tool is invoked.
        agent_id: Optional agent identifier for middleware routing.

    Returns:
        The final output string after the agent run completes.
    """
    if not self._agent:
        return "(No agent configured.)"
    if self._invoke_middleware:
        prompt = await self._invoke_middleware(prompt.strip(), agent_id)
    async with self._lock:
        from agents import Runner
        from openai.types.responses import ResponseTextDeltaEvent

        result = Runner.run_streamed(
            self._agent,
            prompt,
            session=self._session,
        )
        full_text = ""
        async for event in result.stream_events():
            if (event.type == "raw_response_event"
                    and isinstance(event.data, ResponseTextDeltaEvent)):
                full_text += event.data.delta
                await on_chunk(event.data.delta)
            elif (event.type == "run_item_stream_event"
                    and event.item.type == "tool_call_item"
                    and on_tool_call):
                tool_name = getattr(event.item.raw_item, "name", "tool")
                await on_tool_call(tool_name)
        return result.final_output or full_text
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
        await channel.on_stream_start(user_id)
        response = await self.invoke_agent_streamed(
            text,
            on_chunk=lambda chunk: channel.on_stream_chunk(user_id, chunk),
            on_tool_call=lambda name: channel.on_stream_status(user_id, f"Using: {name}"),
        )
        await channel.on_stream_end(user_id, response)
    else:
        response = await self.invoke_agent(text)
        await channel.send_to_user(user_id, response)

    await self._emit("agent_response", {"text": response, ...})
```

The `agent_response` event is emitted after completion in both paths, carrying the full text — no change for event subscribers.

### 4. Loader: No Changes Required

The Loader already detects protocols via `isinstance()` in `detect_and_wire_all()`. `StreamingChannelProvider` is detected automatically when `MessageRouter` checks `isinstance(channel, StreamingChannelProvider)` at message-handling time. No separate registration is needed — the channel is already registered as a `ChannelProvider`.

### 5. ExtensionContext: Streaming Access for Extensions

`ExtensionContext` gains a streaming method so that proactive extensions (heartbeat, schedulers, future agents) can invoke the orchestrator with streaming delivery:

```python
async def invoke_agent_streamed(
    self,
    prompt: str,
    on_chunk: Callable[[str], Awaitable[None]],
    on_tool_call: Callable[[str], Awaitable[None]] | None = None,
) -> str:
    """Ask the agent to process a prompt with streaming callbacks.
    Returns final_output after the agent run completes."""
    return await self._router.invoke_agent_streamed(
        prompt, on_chunk=on_chunk, on_tool_call=on_tool_call
    )
```

This mirrors the router's callback-based API. Existing extensions that call `invoke_agent()` are unaffected.

### 6. Manifest: Optional Streaming Configuration

Streaming behavior is controlled by protocol implementation, not by manifest. However, channels may need transport-specific tuning. The existing `config:` block in `manifest.yaml` is sufficient:

```yaml
# cli_channel/manifest.yaml
config:
  streaming_enabled: true          # allows disabling streaming for debugging

# telegram_channel/manifest.yaml
config:
  polling_timeout: 30
  streaming_enabled: true
  stream_edit_interval_ms: 500     # how often to edit the Telegram message
  stream_min_chunk_chars: 20       # minimum characters before an edit
```

No schema changes to `ExtensionManifest`. The `config:` dict is already a free-form `dict` that channels read via `context.get_config()`. Channels check `streaming_enabled` in their `StreamingChannelProvider` methods and can fall back to accumulating the full text for a single `send_to_user()` call when streaming is disabled.

### 7. Channel Implementations

#### CLI Channel

Minimal change — implement the four streaming methods:

```python
class CliChannelExtension:
    # ... existing ChannelProvider methods unchanged ...

    # StreamingChannelProvider
    async def on_stream_start(self, _user_id: str) -> None:
        pass

    async def on_stream_chunk(self, _user_id: str, chunk: str) -> None:
        print(chunk, end="", flush=True)

    async def on_stream_status(self, _user_id: str, status: str) -> None:
        print(f"\n  [{status}]", flush=True)

    async def on_stream_end(self, _user_id: str, _full_text: str) -> None:
        print()
        print()
```

#### Telegram Channel

Telegram simulates streaming via progressive message editing. The Bot API has rate limits (~30 requests/sec globally, ~1 edit/sec per chat for `editMessageText`), so edits are debounced with a configurable interval (default 500ms).

**Per-user stream state.** Although the current implementation is single-user (one `chat_id`), the streaming state is keyed by `user_id` to avoid a costly refactor when multi-user support is added:

```python
@dataclass
class StreamState:
    """Active streaming session for one user."""
    message_id: int
    buffer: str = ""
    last_edit_at: float = 0.0
```

```python
class TelegramChannelExtension:
    # ... existing code ...
    _streams: dict[str, StreamState]  # keyed by user_id

    async def on_stream_start(self, user_id: str) -> None:
        msg = await self._bot.send_message(chat_id=self._chat_id, text="...")
        self._streams[user_id] = StreamState(message_id=msg.message_id)

    async def on_stream_chunk(self, user_id: str, chunk: str) -> None:
        state = self._streams.get(user_id)
        if not state:
            return
        state.buffer += chunk
        now = time.monotonic()
        interval = self._stream_edit_interval_ms / 1000
        min_chars = self._stream_min_chunk_chars
        if (now - state.last_edit_at >= interval
                and len(state.buffer) >= min_chars):
            try:
                await self._bot.edit_message_text(
                    chat_id=self._chat_id,
                    message_id=state.message_id,
                    text=state.buffer,
                )
                state.last_edit_at = now
            except Exception:
                pass  # rate-limited or network error; next chunk will retry

    async def on_stream_status(self, user_id: str, status: str) -> None:
        await self._bot.send_chat_action(chat_id=self._chat_id, action="typing")

    async def on_stream_end(self, user_id: str, full_text: str) -> None:
        state = self._streams.pop(user_id, None)
        if not state:
            return
        try:
            await self._bot.edit_message_text(
                chat_id=self._chat_id,
                message_id=state.message_id,
                text=full_text,
            )
        except Exception as e:
            if self._ctx:
                self._ctx.logger.exception("Final stream edit failed: %s", e)
```

Users see text appearing in ~500ms–1s bursts. Not token-by-token smooth like browser-based AI chats, but clearly "alive" — far better than 10+ seconds of silence. This is how most Telegram AI bots work in practice.

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
| 2 | `core/extensions/router.py` | Add `invoke_agent_streamed()` (callback-based), branch in `handle_user_message()` |
| 3 | `core/extensions/manifest.py` | Document `streaming_enabled`, `stream_edit_interval_ms` config conventions |
| 4 | `sandbox/extensions/cli_channel/main.py` | Implement `StreamingChannelProvider` (4 methods) |
| 5 | End-to-end test | Verify CLI streaming works, non-streaming channels unaffected |
| 6 | `sandbox/extensions/telegram_channel/main.py` | Implement `StreamingChannelProvider` with `StreamState` and edit-debounce |
| 7 | `core/extensions/context.py` | Add `invoke_agent_streamed()` for extension access |

## Consequences

- **Users see responses as they are generated.** CLI prints token-by-token. Telegram shows a progressively updating message via edit-debounce. The "dead air" problem is eliminated for streaming-capable channels.
- **Non-streaming channels require zero changes.** The `ChannelProvider` protocol and `send_to_user()` contract are untouched. Existing extensions continue to work identically.
- **Channels control their own presentation.** The four-method lifecycle gives each transport full control over how chunks are buffered, debounced, and displayed. The kernel does not impose a delivery strategy.
- **Lock safety is guaranteed by design.** The callback-based `invoke_agent_streamed()` acquires and releases the lock internally. No caller can accidentally leak the lock or create a window between invocation and iteration. This is a deliberate choice over returning an `AsyncIterator`, which would require the caller to manage lock scope correctly.
- **SDK dependency is contained.** Only `MessageRouter.invoke_agent_streamed()` imports `ResponseTextDeltaEvent` from the OpenAI SDK. Channel extensions never see SDK types — they receive plain `str` chunks via callbacks.
- **No new external dependencies.** `Runner.run_streamed()` is part of the already-used `openai-agents` SDK. No additional packages are required.
- **Event contract is stable.** All existing event subscribers (memory, logging, etc.) continue to receive the complete `agent_response` after streaming finishes. No partial-event mechanism is introduced.
- **Multi-user ready.** Telegram channel's `StreamState` is keyed by `user_id` from day one, avoiding a refactor when multi-user support is added.
- **Future extensibility.** The `on_stream_status()` method can be extended to carry structured data (tool name, progress percentage) when needed. The `StreamingChannelProvider` protocol can gain additional lifecycle methods (e.g., `on_stream_cancel()`) without breaking existing implementations. The callback API in `ExtensionContext` enables proactive extensions (heartbeat, scheduler-triggered agents) to stream responses through channels.
