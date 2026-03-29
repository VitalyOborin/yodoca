# ADR 039: LLM-Native Outreach Planner

## Status

Proposed.

## Context

Stages 0–6 of the soul extension implemented the companion's "body": drives,
homeostasis, phase resolution, perception, temperament, initiative budget,
boundary governor, and presence UI. The system correctly decides **whether and
when** to reach out. But the content of proactive outreach is almost entirely
hardcoded — static strings keyed by phase, with a narrow LLM path only for
5 predefined discovery topics in DISCOVERY lifecycle.

The blueprint (`docs/research/soul.md`) explicitly calls for LLM-generated
outreach text (estimated at 1–3 calls/day, ~$0.01–0.03) and describes the
Discovery mode as "направленный диалог, где каждый вопрос вытекает из
предыдущего ответа". The current implementation does not deliver this:

- `_build_outreach_text` in `main.py` is a `match phase` lookup returning
  one of 5 hardcoded English sentences.
- `discovery_runtime.maybe_build_outreach()` generates LLM questions but only
  when phase is CURIOUS or SOCIAL, lifecycle is DISCOVERY, and a lowest-
  coverage topic is found. Outside these conditions it returns `None` and the
  fallback strings fire.
- Outreach is fire-and-forget via `notify_user` — the companion sends a
  message but has no follow-up context, no conversational thread, and no
  ability to reference what it was "thinking about".
- Reflections and explorations are stored as traces but never surface to the
  user — they cannot become outreach content.

The consequence: the companion has rich internal life but communicates like a
notification system. A hardcoded "I was thinking about one small thing." is
not proactivity — it is a cron job with feelings. Two people meeting for the
first time don't exchange template strings — they explore, question, share,
follow up.

### Alternatives considered

1. **Improve static strings** — add more per-phase variants with
   randomization. Rejected: still no context, no personality, no memory of
   past conversations. Lipstick on a template.

2. **Expand discovery_runtime coverage** — make it work in all phases, all
   lifecycles, with more topics. Rejected: the 5-topic checklist model
   is inherently limited; real conversations are open-ended.

3. **Full LLM OutreachPlanner** — a dedicated runtime that gathers context,
   selects intent, and generates a natural message using LLM. Accepted.

## Decision

### 1. Replace `_build_outreach_text` with an OutreachPlanner runtime

A new module `outreach_planner.py` replaces the static text generation path.
The module follows the same pattern as `reflection_runtime.py` and
`exploration_runtime.py`: a self-contained runtime with its own agent,
lifecycle management (`try_create_agent`, `destroy`), and budget integration.

The planner is invoked when the boundary governor says ALLOW and the main.py
outreach flow reaches the text generation step. If the planner fails or LLM
is unavailable, a minimal static fallback is used (degraded mode, per
Axiom 5: Graceful Presence).

### 2. Outreach is context-driven, not template-driven

Before generating a message, the planner assembles an **outreach context
packet** from existing storage (no new tables required):

| Source | What | Purpose |
|--------|------|---------|
| `interaction_log` | Last N interactions (direction, length, channel, timestamp) | Recency and rhythm of conversation |
| `traces` | Recent traces (phase transitions, reflections, explorations) | What the companion has been "thinking about" |
| `discovery_nodes` | Known and unknown topics | Discovery-aware questions |
| `relationship_patterns` | Permanent patterns | Depth of relationship |
| `CompanionState` | Phase, mood, temperament, lifecycle, perception | Personality and current emotional state |
| `soul_metrics` | Recent outreach results (response/ignored/rejected) | Calibrate confidence and tone |

The assembled context is passed to the LLM agent as structured prompt
context. Most query methods already exist in `SoulStorage`; two new
read methods are required (`list_recent_interactions`,
`list_unfollowed_interactions`) and are scoped to task S7-T0. No new
tables are needed — the existing schema is sufficient.

### 3. Outreach has intent, not just text

The planner selects an **outreach intent** before generating text. Intent
determines the kind of interaction the companion wants to initiate. Intents
are not exhaustive categories — they are hints that shape the LLM prompt:

| Intent | Natural when | Example |
|--------|-------------|---------|
| `follow_up` | User mentioned something in recent conversation | "You mentioned X — how did it go?" |
| `share_reflection` | Companion generated a reflection in REFLECTIVE phase | "I was thinking about what you said about X..." |
| `curious_question` | CURIOUS phase, open relationship trend | "What got you into [their field]?" |
| `gentle_checkin` | Care impulse high, user has been quiet | "Hey, just checking in. No pressure." |
| `discovery_question` | DISCOVERY lifecycle, unknown topics | "What does a typical day look like for you?" |
| `continue_thread` | Recent conversation had unresolved topic | "We never finished talking about..." |

Intent selection is rule-based (deterministic mapping from
`phase × lifecycle × relationship_trend × available_context`), not an
additional LLM call. This keeps the cost at one LLM call per outreach.

### 4. Temperament shapes the voice

The LLM prompt includes a compact personality directive derived from the
`TemperamentProfile`:

- High sociability → warmer, less hedging
- High depth → deeper questions, less small talk
- High playfulness → lighter tone, tangential connections
- High caution → more tentative, always offers an exit
- High sensitivity → notices emotional cues, softer

This is not a separate LLM call — it's a few sentences in the system prompt
of the outreach agent.

### 5. Outreach optionally starts a conversation thread

Currently `notify_user` is fire-and-forget. The planner may optionally
suggest a `thread_topic` alongside the message text. If the channel supports
threading (web_channel), the outreach creates a new thread context so the
user can reply naturally and the agent can continue the conversation with
the outreach context preserved.

This is not mandatory for the first implementation — fire-and-forget via
`notify_user` remains the default transport. Thread integration is a
follow-up enhancement.

### 6. Discovery mode subsumes discovery_runtime outreach

The current `discovery_runtime.maybe_build_outreach()` with its 5-topic
keyword detection is absorbed into the planner. The planner uses the same
`DiscoveryTopicCoverage` data but generates questions through the broader
outreach agent prompt rather than a separate narrow agent. The discovery
runtime continues to handle lifecycle transitions and topic registration
(these are not outreach concerns).

### 7. Inference budget

One LLM call per outreach attempt. With daily_budget of 1–5 depending on
lifecycle, this is 1–5 calls/day. The `soul` model slot (typically a
cheaper/faster model) is used. The planner respects the existing
`can_use_llm_fn` / `note_llm_call_fn` recovery integration.

If LLM is unavailable (degraded mode), the planner returns a short generic
message acknowledging the companion's intent without content specificity.
This is the only remaining use of static text.

## Consequences

### Positive

- Outreach becomes genuinely contextual — every message references real
  conversation history, current soul state, and personality.
- Discovery mode produces natural dialogue instead of walking through
  a 5-topic checklist.
- Reflections and explorations gain a path to the user — the companion can
  share what it has been "thinking about".
- The same architecture scales to FORMING and MATURE lifecycles without
  adding per-phase templates.
- Temperament actually affects how the companion communicates, not just
  internal drive dynamics.

### Trade-offs

- Every outreach now requires one LLM call (vs zero for static strings).
  At 1–5 calls/day with a cheap model, this is ~$0.01–0.05/day.
- Outreach quality depends on LLM prompt quality. Poor prompts →
  generic-sounding messages. Requires iterative prompt tuning.
- The outreach agent prompt needs access to conversation history, which
  means the storage layer must provide efficient recent-interaction queries.
  Most exist (`list_traces_since`, `list_discovery_nodes`); two new methods
  are added in S7-T0 (`list_recent_interactions`, `list_unfollowed_interactions`).
- Static fallbacks become a degraded-mode-only path, which means they will
  be exercised rarely and may bitrot. Acceptable: degraded mode is already
  designed to be minimal.

### Non-goals

- Multi-turn outreach planning (plan a sequence of messages). One message
  at a time.
- Outreach text in the user's detected language (use the LLM's natural
  language matching via context, not explicit detection).
- Replacing the boundary governor or initiative budget logic. The decision
  of **when** to reach out stays rule-based. Only **what to say** changes.

## References

- `docs/research/soul.md` — sections "Контур 5: Initiative Policy",
  "Phase 1: Discovery", "Inference Budget"
- `docs/research/soul_outreach_planner.md` — detailed design and task plan
- `docs/adr/038-soul-companion-runtime.md` — parent ADR for soul subsystem
- `sandbox/extensions/soul/discovery_runtime.py` — current discovery outreach
- `sandbox/extensions/soul/main.py` — current `_build_outreach_text`
