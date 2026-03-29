# Soul Outreach Validation

Stage 7 E2E validation is driven by [`scripts/soul_outreach_validation.py`](/C:/www/projects/zynero/assistant4/scripts/soul_outreach_validation.py).

## Scenarios

1. `DISCOVERY question`
   - lifecycle: `DISCOVERY`
   - expectation: one natural getting-to-know-you question tied to a missing topic

2. `FORMING follow-up`
   - lifecycle: `FORMING`
   - expectation: remembers an unfinished user thread instead of asking a generic question

3. `MATURE reflection sharing`
   - lifecycle: `MATURE`
   - expectation: shares a short reflection, not a template ping

4. `Degraded fallback`
   - expectation: intent-aware fallback, no generic phase-based placeholder

5. `Russian tone check`
   - expectation: output stays in Russian and reflects a quieter temperament

## Run

```bash
uv run python scripts/soul_outreach_validation.py
```

## Acceptance

- No output should resemble `I was thinking about one small thing.`
- Discovery output should ask about a real gap, not repeat known topics.
- Follow-up output should feel connected to prior context.
- Reflection output should sound like a thought, not advice or a notification.
- Degraded output should still be intent-shaped and minimal.
- Russian scenario should produce Russian output.

## Latest Run

Validated locally on `2026-03-29` with `uv run python scripts/soul_outreach_validation.py`.

- `DISCOVERY question`: passed, natural question tied to an unknown topic.
- `FORMING follow-up`: passed after adding anti-placeholder prompt guardrail.
- `MATURE reflection sharing`: passed, reflective rather than assistant-like.
- `Degraded fallback`: passed, intent-aware fallback instead of phase-based placeholder.
- `Russian tone check`: passed, output stayed in Russian.
