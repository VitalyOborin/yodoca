# Soul Extension — Task-Level Delivery Plan

> Reference: [soul.md](soul.md) — architectural blueprint, [soul_development.md](soul_development.md) — development plan & staged roadmap.

## Принцип декомпозиции

Работа нарезана на `task slices` по 8–12 часов, где каждая задача даёт проверяемый артефакт: код, тесты, схему БД, симулятор, метрики, acceptance scenario. Не делать задач вида "реализовать Stage 1" целиком. Правильный размер: одна задача = один замкнутый технический результат.

> **Общий объём:** 45 задач × 8–12h = 360–540 часов ≈ 9–14 недель чистого кодирования.
> С буфером (+40% на review, debugging, parametric tuning): **13–19 недель**.

Ниже — полный backlog в правильной последовательности с блокерами, параллельностью, рисками и ожидаемым результатом.

***

## Stage 0 — Foundation

### 1. `S0-T1a` Anti-patterns + event namespace [~4h]

Зафиксировать anti-patterns list (что агент никогда не делает) и event namespace `companion.*` (какие events soul публикует).

Блокеры: нет.
Параллельно: ничего.
Риски: слишком абстрактный результат без конкретных примеров.
Внимание: anti-patterns должны быть testable утверждениями, не пожеланиями.
Результат: два зафиксированных артефакта, от которых зависят дизайн-решения во всех stages.

### 2. `S0-T1b` ADR + soul/memory boundary + Stage Dependency Contract [~8h]

Написать ADR: архитектурные инварианты soul, граница с memory (только через public API, без прямого доступа к `memory.db`), Stage Dependency Contract (`depends_on` в manifest по stages), evaluation plan (какие метрики, как измеряем, thresholds).

Блокеры: `S0-T1a`.
Параллельно: ничего.
Риски: размытые границы между soul и memory.
Внимание: ADR должен быть конкретным — если после прочтения разработчик задаёт вопрос "а можно ли X?", ADR неполный.
Результат: утверждённый design baseline, после которого можно кодировать без постоянных переобсуждений.

### 3. `S0-T2` Minimal `soul.db` schema + storage contract [8–12h]

Описать и создать DDL для `soul_state`, `traces`, `interaction_log`, `soul_metrics`. Определить write patterns, индексы, TTL cleanup hooks.

Блокеры: `S0-T1b`.
Параллельно: можно с `S0-T3`.
Риски: болезненная миграция позже, если схема не отражает write patterns.
Внимание: timestamps, WAL mode, индексы на `created_at` для TTL, cleanup hooks.
Результат: готовая схема и storage API contract.

### 4. `S0-T3` Core state model package [8–12h]

Реализовать dataclasses/модели: `CompanionState`, `HomeostasisState`, phase enums, presence enums, serialization (JSON round-trip).

Блокеры: `S0-T1b`.
Параллельно: можно с `S0-T2`.
Риски: слишком рано зацементировать плохую модель.
Внимание: чётко отделить temperament (slow, persistent), fast state (drives, phase), metrics (aggregated).
Результат: стабильный state contract для всех следующих задач.

### 5. `S0-T4` Drive dynamics engine [8–12h]

Реализовать рост drives, coupling matrix, hysteresis (`HYSTERESIS_MARGIN=0.15`), `MIN_DWELL_TIME`, circadian modulation, phase resolution.

Блокеры: `S0-T3`.
Параллельно: нет.
Риски: численная нестабильность (R2 из soul_development.md).
Внимание: **чистые функции, без I/O.** `tick(dt, circadian_modifier) → new_state`. Должен быть runnable без extension context.
Результат: deterministic engine, который можно гонять вне extension.

### 6. `S0-T5` Offline simulator CLI [8–12h]

Python-скрипт (не extension), который прогоняет synthetic days/weeks и строит phase distribution, state traces, anomaly counters, coupling heatmap.

Блокеры: `S0-T4`.
Параллельно: нет.
Риски: если не сделать сейчас, tuning станет слепым.
Внимание: reproducible seeds, configurable synthetic event profiles (chatty user, silent user, erratic user).
Результат: инструмент для калибровки поведения **до** интеграции.

### 7. `S0-T6` Simulation test suite [8–12h]

Набор unit/integration tests на 24h/7d/30d synthetic scenarios. Тесты на bounds (`drives ∈ [0.05, 0.95]`), phase diversity (≥3 phases/day), no oscillation, no stuck phase, coupling stability.

Блокеры: `S0-T5`.
Параллельно: **нет. Hard gate на Stage 1.** Если тесты не прошли — фиксить drives, не строить extension.
Риски: недостаточное покрытие runaway/stuck scenarios.
Внимание: tests на bounds, diversity, no oscillation. Включить edge cases: 48h silence, 100 messages/hour burst.
Результат: safety net для последующего tuning. Без зелёных тестов — нет перехода к Stage 1.

***

## Stage 1 — Living Runtime MVP

### 8. `S1-T1` Extension scaffold [8–12h]

Создать `sandbox/extensions/soul/` с `manifest.yaml`, `main.py`, `schema.sql`, базовым lifecycle (init, startup, shutdown).

Блокеры: `S0-T1b`, `S0-T2`.
Параллельно: можно с `S1-T2`.
Риски: неверный manifest contract.
Внимание: `depends_on: [kv]` только на MVP (memory добавляется в Stage 3, см. Stage Dependency Contract).
Результат: extension грузится и проходит lifecycle.

### 9. `S1-T2` Storage layer implementation [8–12h]

Реализовать `storage.py` для `soul.db`: init schema, load/save state, append trace (thresholded — stub для write policy), update metrics, TTL cleanup hooks.

Блокеры: `S0-T2`, `S0-T3`.
Параллельно: можно с `S1-T1`.
Риски: race conditions при частых записях.
Внимание: atomic writes, idempotent init, WAL mode. Concurrent access support (для multi-channel в Stage 5).
Результат: стабильный persistence backend.

### 10. `S1-T3` Wake-up protocol [8–12h]

Реализовать restore after restart. Gap calculation: `now - last_tick_at`. Сценарии: `<5m` (continue), `1-12h` (accelerated catch-up), `12h+` (reset to baseline).

Блокеры: `S1-T2`, `S0-T4`.
Параллельно: нет.
Риски: неконсистентный resume после долгого офлайна.
Внимание: отдельные сценарии `<5m`, `1h+`, `12h+`. Логирование gap duration.
Результат: предсказуемое восстановление состояния.

### 11. `S1-T4` Inner loop MVP [8–12h]

Собрать `run_background()` с tick → drive update → phase check → presence update → persist → error handling. При phase transition — emit `companion.phase.changed` event через EventBus (foundation для Stage 6 Presence UI).

Блокеры: `S1-T1`, `S1-T2`, `S1-T3`, `S0-T4`.
Параллельно: нет.
Риски: loop stability, silent hang.
Внимание: один tick не должен валить весь сервис (`try/except` per tick). Circadian modifier: `now().hour → circadian_modifier()` подключает реальные часы к drive engine.
Результат: агент "живёт" в фоне без outreach. Phase events публикуются для подписчиков.

### 12. `S1-T4b` Healthcheck hung-loop detection [~3h]

Реализовать `healthcheck()`: проверяет `now - last_tick_at > 2 * tick_interval` → returns `False`. Loader остановит и перезапустит extension.

Блокеры: `S1-T4`.
Параллельно: можно с `S1-T5`.
Риски: если `run_background()` завис (ждёт LLM response без timeout), healthcheck вернёт `True`, но loop не тикает — silent degradation.
Внимание: `last_tick_at` обновляется в начале tick, не в конце. Иначе долгий tick выглядит как hang.
Результат: автоматическая защита от hung loops.

### 13. `S1-T5` Heuristic perception v0 [8–12h]

Zero-inference perception signals из router events: message length, frequency, response latency, question marks, emoji presence, brevity. Probabilistic floats, not booleans.

Блокеры: `S1-T1`.
Параллельно: можно с `S1-T4`.
Риски: переоценка слабых сигналов.
Внимание: это только weak signals. Не делать уверенных выводов из одного сообщения. Каждый signal — `float [0.0, 1.0]`.
Результат: базовое обновление perception без LLM.

### 14. `S1-T6` Event subscriptions wiring [8–12h]

Подписать soul на `user_message` и `agent_response`, обновлять state/perception/social_hunger.

**Механизм подписки (design decision):**
- `ctx.subscribe("user_message", handler)` — **MessageRouter** (in-memory, синхронный hot path). Для real-time perception updates, social_hunger satiation. Аналогично тому, как memory подписывается.
- `ctx.subscribe_event("session.completed", handler)` — **EventBus** (SQLite journal, async). Для consolidation triggers, interaction_log writes. Допустима задержка до `poll_interval`.

Блокеры: `S1-T1`, `S1-T5`.
Параллельно: можно с `S1-T7`.
Риски: спутать `subscribe` (MessageRouter) и `subscribe_event` (EventBus) — разная семантика, разная latency.
Внимание: perception должна обновляться мгновенно (MessageRouter), не с задержкой 0–5s (EventBus).
Результат: soul начинает реагировать на диалог в реальном времени.

### 15. `S1-T7` ContextProvider MVP [8–12h]

Короткая context injection: phase, mood, one-line note. Hard limit ≤ 200 tokens. Jinja2 template с phase-specific markers.

Блокеры: `S1-T3` (нужен state для injection).
Параллельно: можно с `S1-T6`.
Риски: bloated context.
Внимание: hard limit по размеру. Context priority = 60 (выше memory's 50).
Результат: ответы агента начинают менять тон по состоянию.

### 16. `S1-T8` ToolProvider MVP [8–12h]

`get_soul_state`: текущие drives, фаза, mood, uptime, health. Debug-friendly output для ручной проверки и мониторинга.

Блокеры: `S1-T1`, `S1-T2`.
Параллельно: да, с любыми S1-T*.
Риски: слишком бедная диагностика.
Внимание: вывод должен быть удобен для ручной проверки. Включить: drive values, phase, time in phase, last tick, tick count.
Результат: разработчик видит живой runtime изнутри.

### 17. `S1-T9` 24h soak validation [8–12h]

Прогон на длительной сессии: логи, crash recovery, state persistence, morning/evening tone diff. Проверка acceptance criteria Stage 1 из soul_development.md.

Блокеры: все `S1-*`.
Параллельно: нет.
Риски: скрытые loop crashes, silent degradation.
Внимание: **gate на выход из MVP runtime.** Не переходить к Stage 2 без зелёного прогона.
Результат: доказательство `proof of life`.

***

## Stage 2 — Controlled Initiative

### 18. `S2-T0` Outreach correlation design [~4h]

Зафиксировать design decision для correlation outreach → user reply. Soul отправляет outreach через `ctx.notify_user()` (fire-and-forget, без correlation_id). Нужна явная логика определения: response, ignored, timing_miss.

**Design:**
- Outreach записывает `pending_outreach_at` в state.
- `user_message` в течение 60 минут после outreach → `response`.
- Больше 60 минут → `timing_miss`.
- Никакого сообщения до следующего outreach window → `ignored` (после cooldown).

Блокеры: `S0-T1b` (design decisions scope).
Параллельно: можно с `S1-*` (design task, не код).
Риски: ad hoc реализация в S2-T4 без явной спецификации.
Внимание: зафиксировать как design decision в ADR/design artifacts.
Результат: однозначная спецификация для S2-T4.

### 19. `S2-T1` Initiative domain model [8–12h]

Реализовать `InitiativeBudget`, outreach result types (response/ignored/timing_miss), cooldown state machine.

Блокеры: **`S1-T4`** (inner loop — без него initiative model некуда интегрировать).
Параллельно: можно с `S2-T2`.
Риски: неучтенные edge cases ignored vs timing_miss.
Внимание: state machine outreach lifecycle. Cooldown rules: 6h after ignored (if available), 2d after explicit rejection.
Результат: формализованная модель инициативы.

### 20. `S2-T2` User presence estimation v0 [8–12h]

Реализовать `last_interaction_at`, hour/day patterns, `estimated_availability` [0.0–1.0].

Блокеры: `S1-T6`, `S1-T2`.
Параллельно: можно с `S2-T1`.
Риски: ложные availability assumptions в первые дни (мало данных).
Внимание: **bias toward unavailable.** Default `estimated_availability = 0.3` пока данных мало. Conservative.
Результат: базовый gating по доступности пользователя.

### 21. `S2-T3` Boundary governor v0 [8–12h]

Hard blocks: night (circadian), RESTING phase, budget exhausted, cooldown active, `estimated_availability < 0.3`.

Блокеры: `S2-T1`, `S2-T2`.
Параллельно: нет.
Риски: или слишком блокирует (agent never speaks), или пропускает лишнее.
Внимание: conservative defaults. Лучше молчать, чем спамить.
Результат: безопасный gate перед outreach.

### 22. `S2-T4` Outreach transport integration [8–12h]

Безопасная проактивная отправка через `context.notify_user()`. Запись attempts/results в `soul.db`. Correlation logic по спецификации из `S2-T0`.

Блокеры: `S2-T3`, `S2-T0`.
Параллельно: нет.
Риски: дубли, некорректная корреляция replies.
Внимание: различать response, ignored, timing_miss по правилам из S2-T0. Idempotent — один outreach per window.
Результат: soul умеет отправлять одно сообщение сам.

### 23. `S2-T5` Controlled initiative logic [8–12h]

Логика: `social_hunger > adaptive_threshold` + governor ALLOW → one-shot outreach. Cooldown application. `daily_budget=1` как hard rule.

Блокеры: `S2-T4`.
Параллельно: нет.
Риски: спам или повторные outreach loops.
Внимание: `daily_budget=1` — непреодолимый hard limit. Даже при баге в threshold logic — не более 1 outreach/day.
Результат: минимально безопасная инициатива.

### 24. `S2-T6` Manual evaluation gate [3–5 дней прогон]

Ручной сценарный прогон 3–5 дней: молчание, ответ, игнор днём, игнор ночью, burst-разговор, рестарт.

Блокеры: все `S2-*`.
Параллельно: нет.
Риски: cognitive bias ("достаточно хорошо") без записанных критериев.
Внимание: **GO/NO-GO критерии записать ДО начала прогона, не после.** Конкретные pass/fail conditions.
Результат: решение, продолжать ли проект. Если нет ощущения "оно живое" — остановиться и пересмотреть.

***

## Stage 3 — Relationship Memory

> **Manifest upgrade:** при входе в Stage 3 обновить `manifest.yaml`: `depends_on: [kv, memory]`. Memory становится read-only зависимостью для context enrichment через public API `context.get_extension("memory")`.

### 25. `S3-T1` Interaction patterns storage [8–12h]

Реализовать агрегации: hour × day_of_week → interaction_count, response_rate, avg_response_delay. Derived patterns для pattern-based availability.

Блокеры: `S2-T2`.
Параллельно: можно с `S3-T2`.
Риски: плохая схема агрегации (raw log vs derived).
Внимание: не только raw log, но и derived patterns. Rolling window aggregation.
Результат: основа для персонального ритма.

### 26. `S3-T2` Relationship trend model [8–12h]

Реализовать `openness_trend` (north-star metric), `message_depth_trend`, `initiative_ratio_trend`. Explainable — каждая метрика должна быть объяснима в одном предложении.

Блокеры: `S1-T6`, `S1-T2`.
Параллельно: можно с `S3-T1`.
Риски: слабая интерпретируемость метрик.
Внимание: метрики должны быть explainable. `openness_trend` — растёт ли глубина и откровенность пользователя со временем.
Результат: появляется north-star telemetry.

### 27. `S3-T3` Mood classifier integration [8–12h]

LLM-based mood/perception classification. **Не per-message — по триггеру:**
- После N сообщений подряд (batch), или
- Раз в 30 минут при активном диалоге, или
- Только при `phase == SOCIAL` (когда perception matters most).

Эвристики из S1-T5 остаются основным источником. LLM — периодическое уточнение, не замена.

Блокеры: **`S1-T5`** (heuristic perception — база), **`S1-T6`** (event subscriptions — откуда приходят сообщения).
Параллельно: можно с `S3-T1`.
Риски: cost (20-50 LLM calls/day при per-message) и false confidence.
Внимание: budget guard — `max_mood_inferences_per_day` в config. Atomic tracking в `soul_metrics.inference_count`.
Результат: perception становится богаче, но экономно.

### 28. `S3-T4` Sliding window perception engine [8–12h]

Накапливать perception signals по последним N сообщениям. Decay + smoothing — недавние сообщения весят больше.

Блокеры: `S3-T3`.
Параллельно: нет.
Риски: одно сообщение доминирует в window.
Внимание: exponential decay, configurable window size, outlier dampening.
Результат: более устойчивый perception layer.

### 29. `S3-T5` Context enrichment v2 [8–12h]

Добавить короткие relation-aware notes в ContextProvider: "Note: user seems tired today", "User prefers brief exchanges in evenings". Hard limit ≤ 200 tokens сохраняется.

Блокеры: `S3-T2`, `S3-T4`.
Параллельно: нет.
Риски: prompt inflation.
Внимание: relevance gate — не добавлять note, если confidence низкий. Quality > quantity.
Результат: агент ведёт себя "про этого пользователя".

### 30. `S3-T6` Metrics and observability pack [8–12h]

`soul_metrics` daily aggregation, `get_soul_metrics` tool, monitoring: context size, outreach quality, perception corrections, openness_trend. Day/week views.

Блокеры: `S3-T1`, `S3-T2`.
Параллельно: да.
Риски: без метрик Stage 4+ станет слепым.
Внимание: день/неделя срезы. Self-correction trigger: `openness_trend` falling 2+ weeks → alert.
Результат: measurable relationship runtime. Без этого — Stage 4 нельзя начинать.

***

## Stage 4 — Personality Formation

### 31. `S4-T1` TemperamentProfile implementation [8–12h]

`TemperamentProfile` dataclass + persistence. Seed/default values (all 0.5). Integrity rules: variance < 0.05 → drift rejected (personality erosion protection).

Блокеры: `S0-T3`.
Параллельно: можно с `S4-T2`.
Риски: смешение personality (slow, stable) и state (fast, volatile).
Внимание: temperament drift rate: 0.05 → 0.03 → 0.01 → 0.003 (decaying over months).
Результат: устойчивый базовый характер.

### 32. `S4-T2` Setup/onboarding questionnaire [8–12h]

Опциональная инициализация temperament через `SetupProvider`. Natural language questions, not sliders. Sane defaults if skipped.

Блокеры: `S4-T1`.
Параллельно: можно с `S4-T3`.
Риски: UI/schema ripple (SetupProvider может потребовать `choices` support).
Внимание: optional path и sane defaults. Пользователь может пропустить → всё работает.
Результат: управляемый seed характера.

### 33. `S4-T3` Consolidation pipeline v1 [8–12h]

Relationship_pattern detection (≥3 repetitions → permanent). Weekly identity_shift. TTL cleanup. Triggers temperament drift.

Блокеры: `S3-T1`, `S3-T2`, `S4-T1`.
Параллельно: нет.
Риски: случайный drift и мусорные patterns.
Внимание: thresholds before permanence. Не каждое наблюдение → pattern.
Результат: character starts forming from use.

### 34. `S4-T4` Thresholded trace writing [8–12h]

Реализовать write policy по значимым событиям (**не per-tick**):
- Phase transition
- Perception shift: `|new - old| > 0.2`
- Outreach attempt/result
- Exploration result (new insight)
- Drive hitting boundary (`< 0.1` или `> 0.9`)
- User interaction event

Ожидаемый объём: 20–80 traces/day (vs ~2880 при per-tick).

Блокеры: `S1-T2`, `S1-T4`.
Параллельно: можно с `S4-T3`.
Риски: слишком много или слишком мало traces.
Внимание: event significance thresholds. Err on the side of fewer, higher-quality traces.
Результат: качественная короткоживущая память.

### 35. `S4-T5` Reflection generator [8–12h]

Генерация reflections в REFLECTIVE phase. LLM-based, с budget limits (5–10 reflections/day max). Short functional reflections, not theatrical monologues.

Блокеры: `S3-T3`, `S4-T4`.
Параллельно: нет.
Риски: theatricality и runaway loops.
Внимание: short functional reflections. Budget guard: `max_reflections_per_day`.
Результат: появляется внутренняя рефлексия.

### 36. `S4-T6` Exploration Tier 0 [8–12h]

Исследование собственной памяти/trace space: FTS по traces/reflections. Без web и user files.

Блокеры: `S4-T4`, `S4-T5`.
Параллельно: нет.
Риски: curiosity runaway.
Внимание: novelty exhaustion — 3 цикла без нового → curiosity forced down. Hard limits на LLM calls per CURIOUS cycle.
Результат: безопасный внутренний exploration loop.

### 37. `S4-T7` Exploration Tier 1 — User KB [8–12h]

Opt-in exploration пользовательской базы знаний. Consent boundary в `soul.db`. Max 3 files/cycle.

Блокеры: `S4-T6`, `S0-T1b` (consent model from ADR).
Параллельно: можно с `S4-T8`.
Риски: privacy violation без явного consent.
Внимание: explicit opt-in. Consent записывается в `soul.db`, отзывается через команду/config.
Результат: агент знает контекст пользователя (если разрешено).

### 38. `S4-T8` Exploration Tier 2 — Web Discovery [8–12h]

Opt-in web exploration. Max 2 queries/day. Budgeted, logged.

Блокеры: `S4-T6`.
Параллельно: можно с `S4-T7`.
Риски: runaway web queries, irrelevant results.
Внимание: hard daily limit, novelty exhaustion applies. Результаты web → soul traces, не memory.
Результат: агент может находить новое во внешнем мире (если разрешено).

***

## Stage 5 — Full Runtime

### 39. `S5-T1` Discovery FSM [8–12h]

Реализовать `DISCOVERY → FORMING → MATURE` как **explicit FSM** (enum states + transition rules). Natural question generation (LLM). Discovery memory nodes. Exit condition: основные темы покрыты OR 20+ interactions. Плавный interpolation при переходах.

Блокеры: `S4-T1`, `S3-T2`.
Параллельно: можно с `S5-T2`.
Риски: prompt-driven chaos вместо structured transitions.
Внимание: explicit transitions, not if/else chains.
Результат: осмысленный cold start companion.

### 40. `S5-T2` Failure recovery package [8–12h]

Mood mean reversion к temperament baseline (mood floor -0.3). Stuck phase reset (`MAX_DWELL_TIME` per phase). No-LLM mode (drives tick, presence updates, no reflections). Exploration runaway protection (max 10 LLM calls per CURIOUS cycle).

Блокеры: `S1-T4`, `S3-T3`, `S4-T5`.
Параллельно: можно с `S5-T1`.
Риски: silent degradation bugs.
Внимание: **each failure mode should have a test scenario.** Не "мы думаем что работает", а "тест подтвердил".
Результат: устойчивость long-running runtime.

### 41. `S5-T3` Multi-channel identity support [8–12h]

Один soul state across всех channels. Channel preferences as metadata, not separate state. `soul.db` — single source of truth.

Блокеры: **`S1-T2`** (storage — concurrent access support), **`S1-T4`** (inner loop — channel-agnostic), `S3-T1` (interaction patterns — need per-channel aggregation).
Параллельно: можно с `S5-T2`.
Риски: fragmented state by channel.
Внимание: one identity, many surfaces. Channel-specific behaviour = presentation layer only.
Результат: единый companion runtime.

### 42. `S5-T4` Transparency and self-monitoring tools [8–12h]

`get_soul_metrics` tool (expanded), raw state exposure mode, self-correction triggers (`openness_trend` falling → identity_shift review).

Блокеры: `S3-T6`, `S4-T3`.
Параллельно: да.
Риски: low observability of emergent behavior.
Внимание: tools for debugging and trust. Пользователь может запросить полное состояние.
Результат: зрелая introspection surface.

***

## Stage 6 — Presence UI

### 43. `S6-T1` Full companion event surface [8–12h]

Расширить event publishing до полной поверхности: `companion.outreach.attempted`, `companion.outreach.result`, `companion.reflection.created`, `companion.lifecycle.changed`.

> Basic phase events (`companion.phase.changed`, `companion.presence.updated`) уже emit из `S1-T4`. Эта задача добавляет остальные domain events.

Блокеры: `S2-T4` (outreach events), `S4-T5` (reflection events).
Параллельно: можно начинать S6-T2/T3 по базовым phase events из S1-T4, не дожидаясь полной event surface.
Риски: noisy event stream.
Внимание: emit only meaningful updates, не каждый tick.
Результат: полная доменная event surface.

### 44. `S6-T2` Web presence surface [8–12h]

SSE/WebSocket transport и UI rendering для presence state. Visual indicators для SILENT / AMBIENT / WARM / WITHDRAWN / PLAYFUL / REFLECTIVE.

Блокеры: `S1-T4` (для базовых phase events). `S6-T1` для полной event surface.
Параллельно: можно с `S6-T3`.
Риски: UI overexposes internal state.
Внимание: presence, not telemetry dump. Пользователь видит "настроение", не drive values.
Результат: companion feels present without text.

### 45. `S6-T3` CLI/Telegram lightweight presence [8–12h]

Status line в CLI. Typing indicator / limited status hints в Telegram.

Блокеры: `S1-T4` (для базовых phase events). `S6-T1` для полной event surface.
Параллельно: да, с `S6-T2`.
Риски: channel-specific awkwardness.
Внимание: minimal non-intrusive display.
Результат: cross-channel silent presence.

***

## Критический путь

```
S0-T1a → S0-T1b → S0-T3 → S0-T4 → S0-T5 → S0-T6 (hard gate)
  → S1-T1 → S1-T4 → S1-T4b → S1-T9 (proof of life gate)
    → S2-T1 → S2-T3 → S2-T4 → S2-T5 → S2-T6 (GO/NO-GO gate)
      → S3-T3 → S3-T4 → S3-T5
        → S4-T3 → S4-T5 → S4-T6
          → S5-T1 / S5-T2
```

Пока не закрыт **S2-T6 GO/NO-GO**, не стоит вкладываться в personality/discovery.

## Что можно делать параллельно

| Пара | Условие |
|------|---------|
| `S0-T2` + `S0-T3` | После `S0-T1b` |
| `S1-T1` + `S1-T2` | После `S0-T6` (hard gate) |
| `S1-T5` + `S1-T7` | Разные зависимости, не пересекаются |
| `S1-T4b` + `S1-T5` | S1-T4b мелкая, S1-T5 независима |
| `S2-T0` + `S1-*` | Design task, не код |
| `S2-T1` + `S2-T2` | Разные домены |
| `S3-T1` + `S3-T2` | Разные модели |
| `S3-T1` + `S3-T3` | Разные зависимости |
| `S4-T1` + `S4-T2` | T2 зависит от T1, но scope позволяет overlap |
| `S4-T3` + `S4-T4` | Разные concerns |
| `S4-T7` + `S4-T8` | Independent tiers |
| `S5-T1` + `S5-T2` | Разные failure domains |
| `S6-T2` + `S6-T3` | Разные channels |
| `S6-T2/T3` + `S2-*` | Базовые phase events из S1-T4 позволяют начать UI с Stage 2 |

***

## На что обратить внимание руководителю

- Не объединять в одну задачу runtime, policy и UX одновременно.
- **Stage 2 должен быть реальной точкой остановки проекта.** GO/NO-GO критерии фиксировать до начала прогона.
- Нужен владелец quality bar, а не только разработчик.
- Численные параметры drives нельзя "подбирать по ощущению" без simulator/tests.
- Все задачи, связанные с инициативой, должны проходить ручной сценарный прогон.
- Наблюдаемость и introspection не вторичны, а обязательны с первых этапов.
- **S0-T6 — hard gate.** Без зелёных simulation tests не начинать extension code.
- **S6 может стартовать раньше** — базовые phase events из S1-T4 позволяют второму разработчику начать Presence UI параллельно со Stage 2.

## Ожидаемый результат по этапам

| Этап | Результат | Gate |
|------|-----------|------|
| Stage 0 | Validated drive dynamics, stable state model, design baseline | S0-T6 simulation tests |
| Stage 1 | Живой фоновой runtime | S1-T9 24h soak |
| Stage 2 | Безопасная инициатива | **S2-T6 GO/NO-GO** |
| Stage 3 | Персонализированное поведение | S3-T6 metrics operational |
| Stage 4 | Формирующийся характер | Temperament differs from seed |
| Stage 5 | Полный companion runtime | Discovery + recovery + metrics |
| Stage 6 | Ощущение присутствия без сообщений | Visual presence in ≥1 channel |
| Stage 7 | Живой, контекстный outreach вместо hardcoded шаблонов | S7-T6 E2E prompt validation |

> **Stage 7** задокументирован в [soul_outreach_planner.md](soul_outreach_planner.md), ADR: [039-soul-outreach-planner.md](../adr/039-soul-outreach-planner.md).

***

***

## Stage 7 — LLM-Native Outreach Planner

> Полный дизайн и задачи: [soul_outreach_planner.md](soul_outreach_planner.md).

Проблема: Stages 0–6 реализовали "тело" компаньона (drives, гомеостаз, фазы, инициатива), но **что сказать** при outreach — это 5 hardcoded строк. Компаньон корректно решает *когда* выходить на связь, но не *о чём* говорить.

Stage 7 заменяет статические строки на LLM-driven OutreachPlanner, который:
1. Собирает контекст из существующего storage (interactions, traces, discovery, temperament)
2. Выбирает intent (follow_up, discovery_question, share_reflection, gentle_checkin, ...)
3. Генерирует natural, personality-driven message через один LLM call

### 46. `S7-T0` Storage read-model upgrade [3–4h]

Добавление read-методов в `SoulStorage`, необходимых для context assembler:
- `list_recent_interactions(limit)` — последние N записей DESC
- `list_unfollowed_interactions(limit, follow_up_window_hours)` — inbound без outbound follow-up
- Возможно: индекс на `(direction, created_at)`
- Unit tests

Блокеры: нет.
Параллельно: можно с S7-T2, S7-T3.
Результат: storage API покрывает нужды assembler.

### 47. `S7-T1` OutreachContext assembler [4–6h]

Dataclass `OutreachContext` + `assemble_outreach_context(state, storage)`. Сборка из 6 storage queries (включая 2 новых из S7-T0).

Блокеры: S7-T0.
Параллельно: можно с S7-T2 если interface согласован.
Результат: тестируемый context assembler.

### 48. `S7-T2` Intent selector [4–6h]

`select_intent(context) → OutreachIntent`. Детерминированный маппинг: phase × lifecycle × trends × available_context → intent.

Блокеры: S7-T1.
Параллельно: нет.
Результат: intent selection, покрытый unit tests.

### 49. `S7-T3` Temperament-to-prompt directive [2–4h]

`build_temperament_directive(profile) → str`. Personality section для LLM prompt на основе TemperamentProfile.

Блокеры: нет.
Параллельно: можно с S7-T0, S7-T1.
Результат: personality bridge к LLM.

### 50. `S7-T4` OutreachPlanner runtime + LLM agent [8–12h]

`outreach_planner.py`: `OutreachPlanner` class с `try_create_agent`, `destroy`, `generate`. System prompt template, per-intent context blocks, degraded mode fallback, LLM generation cap (`max_outreach_llm_calls_per_day = 3`).

Блокеры: S7-T1, S7-T2, S7-T3.
Параллельно: нет.
Результат: working LLM-driven outreach.

### 51. `S7-T5` Integration into main.py [6–8h]

- Инициализация OutreachPlanner в `__init__`, `initialize`, `destroy`
- Замена `_build_outreach_text` на вызов planner
- Удаление outreach-кода из discovery_runtime
- LLM generation cap tracking
- Обновление/переписывание тестов

Блокеры: S7-T4.
Параллельно: нет.
Результат: planner интегрирован, tests зелёные.

### 52. `S7-T6` E2E prompt quality validation [4–6h]

Ручное тестирование outreach quality с реальным LLM по всем lifecycle × intent комбинациям.

Блокеры: S7-T5.
Параллельно: нет.
Результат: качественный outreach, не cringe.

**Общий объём Stage 7:** 31–46 часов.
**Критический путь:** S7-T0 + S7-T3 (parallel) → S7-T1 → S7-T2 → S7-T4 → S7-T5 → S7-T6.

***

## Changelog (review amendments)

Изменения относительно исходного backlog по результатам review:

| # | Изменение | Источник | Обоснование |
|---|-----------|----------|-------------|
| 1 | `S0-T1` split → `S0-T1a` + `S0-T1b` | AI review | Исходная задача ~20h scope, не 8–12h. ADR требует отдельного фокуса. |
| 2 | `S0-T6` — hard gate на Stage 1 | Review | Stage 0 теряет смысл, если S1 стартует до validation. |
| 3 | `S1-T4` — добавлен emit `companion.phase.changed` | AI review | Позволяет S6-T2/T3 стартовать параллельно с Stage 2, экономя 4-6 недель при двух разработчиках. |
| 4 | `S1-T4b` — новая задача (healthcheck hung-loop) | AI review | Защита от silent degradation — hung loop невидим для healthcheck без `last_tick_at` check. |
| 5 | `S1-T6` — explicit subscribe mechanism | AI review | `subscribe` (MessageRouter) vs `subscribe_event` (EventBus) — разная latency, разная semantics. |
| 6 | `S2-T0` — новая задача (outreach correlation design) | AI review | Без спецификации S2-T4 реализует ad hoc корреляцию response/ignored/timing_miss. |
| 7 | `S2-T1` blocker: `S1-T8` → `S1-T4` | Review | Initiative domain model зависит от inner loop, не от ToolProvider. |
| 8 | `S3-T3` blockers: `S1-T5, S2-T1` → `S1-T5, S1-T6` | Review | Mood classifier — perception, не initiative. Зависит от event wiring. |
| 9 | `S3-T3` — добавлен LLM budget guard | AI review | Per-message classification = 20–50 calls/day. Trigger-based invocation экономит бюджет. |
| 10 | `S4-T7` + `S4-T8` — новые задачи (Exploration Tiers 1, 2) | Review | Отсутствовали в исходном backlog. Покрывают soul.md scope. |
| 11 | `S5-T3` blockers: `S3-T1` → `S1-T2, S1-T4, S3-T1` | Review | Multi-channel = concurrent access + channel-agnostic loop. |
| 12 | `S6-T1` — rescoped (basic events → S1-T4, full surface → S6-T1) | AI review | Basic phase events = 10 строк в inner loop. Full surface (outreach, reflection) зависит от later stages. |
| 13 | `S2-T6` — добавлено "критерии записать ДО прогона" | AI review | Защита от cognitive bias при GO/NO-GO evaluation. |
| 14 | Stage 3 — добавлен manifest upgrade note | Review | `depends_on` меняется с `[kv]` на `[kv, memory]` по Stage Dependency Contract. |
| 15 | `S7-T0` — новая задача (storage read-model upgrade) | User review | Storage API не имеет `list_recent_interactions` и `list_unfollowed_interactions`. Без S7-T0 assembler scope занижен. |
| 16 | `S7-T1` blocker: — → S7-T0 | User review | Context assembler зависит от новых storage методов. |
| 17 | `S7-T5` re-estimate: 4–6h → 6–8h | User review | Интеграция + удаление outreach из discovery_runtime + тесты = больше scope. |
| 18 | Stage 7 inference budget: `3–5/day` → `≤3/day` (capped) | User review | Противоречие с blueprint contract `1–3/день`. Добавлен LLM generation cap. |
| 19 | Stage 7 — no backward compatibility | User review | Research project — можно сносить и перестраивать без migration ceremony. |
