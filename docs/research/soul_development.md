# Soul Extension — Development Plan

> Reference: [soul.md](soul.md) — architectural blueprint

## Overview

Soul — самый сложный extension в системе Yodoca. Это не "одна фича", а новый behavioral runtime поверх существующего агентного ядра. По масштабу — R&D-проект уровня крупной подсистемы.

Ключевое архитектурное решение: **soul — отдельная подсистема состояния, не надстройка над memory.** Memory хранит знание и эпизоды. Soul хранит внутреннюю динамику, relationship patterns, инициативу, ритм, метрики. Отдельная БД (`soul.db`), отдельный ContextProvider, отдельная логика. Связь с memory — только через публичное API (`context.get_extension("memory")`), без прямого доступа к `memory.db`.

**Оценка сложности: 8/10.** Архитектура ядра уже поддерживает всё необходимое: `ServiceProvider`, `ContextProvider`, `SchedulerProvider` как протоколы; `context.subscribe()` и `context.subscribe_event()` для подписки на events. Сложность — в правильной реализации динамики, в UX-границе "живой vs навязчивый", и в долгосрочной стабильности поведения.

***

## Сводка оценки

| Метрика | Оценка (оптимистичная) | С буфером (реалистичная) |
|---------|------------------------|--------------------------|
| MVP — proof of life (Stage 0–1) | 2–2.5 недели | 3–3.5 недели |
| v1 — controlled initiative + relationship memory (до Stage 3) | 5–7 недель | 7–9 недель |
| Full scope (soul.md, все stages) | 10–12 недель | 13–16 недель |
| Изменения ядра | Минимальные (0–2 точки расширения) | — |
| Критический path | Stage 0 → 1 → 2 → GO/NO-GO → 3 → 4 → 5 | — |

> **Допущения для оценки:**
> - 1 senior разработчик, знающий кодовую базу Yodoca.
> - Оптимистичная оценка — чистое время кодирования без отвлечений.
> - Реалистичная оценка — +40% буфер на code review, инфраструктурные проблемы, параметрическую настройку, UX-итерации.
> - Stage 0 занимает непропорционально много времени: ADR + schema + simulator + unit-тесты — фундамент, на котором стоит всё.
> - R&D-характер проекта означает, что Stage 4–5 могут потребовать дополнительных итераций (параметры, UX, tuning).
> - GO/NO-GO после Stage 2 — полноценная точка пересмотра. Если не зажглось — roadmap пересматривается.

***

## Риски

### R1: UX — "annoying vs alive" [CRITICAL]

Единственный неотменяемый риск. Система может быть технически правильной, но субъективно восприниматься как спам, театральность или псевдопсихология. Одного неуместного сообщения достаточно для отключения extension навсегда.

**Митигация:**
- MVP начинает с `daily_budget=1` (не 3)
- Первая неделя — наблюдение без outreach (только presence + context injection)
- Бюджет инициативы растёт только при подтверждённом `openness_trend > 0`
- Качественный acceptance test: "пользователь не считает это спамом" — обязательное условие перехода к следующему этапу

### R2: Numerical instability в drive system [HIGH]

Матрица сцепления drives создаёт цепные реакции. Конкретный сценарий: SOCIAL → `reflection_need` растёт (+0.6) → REFLECTIVE → LLM нужен → inference budget исчерпан → агент застревает в "хочет рефлексировать, не может".

**Митигация:**
- Написать offline-симулятор drive system ДО интеграции в extension
- Прогнать 1000 дней с разными seed-значениями, визуализировать phase distribution
- Unit-тесты на 24-часовые симуляции без LLM
- MAX_DWELL_TIME как hard safety net

### R3: Context injection bloat [HIGH]

ContextProvider с priority 60 вставляет soul-контекст в каждый промпт. Если состояние растёт (phase + mood + recent_thought + perception signals), token budget съедается у memory ContextProvider (priority 50, идёт раньше).

**Митигация:**
- Жёсткий лимит: soul context ≤ 200 tokens в 95% случаев
- Мониторинг через `soul_metrics`
- Truncation strategy: если context > лимита, оставить только phase + mood + одну строку note

### R4: Inner Loop stability [HIGH]

`run_background()` должен работать часами/днями. LLM-вызовы, file I/O, SQLite writes — каждый может упасть. В отличие от scheduler (30s tick, простая логика), soul loop содержит больше точек отказа.

**Митигация:**
- Каждый тик в try/except с logging + continue
- LLM-вызовы опциональны (Аксиома 5: Graceful Presence)
- health_check проверяет `last_tick_at`, рестартует loop при зависании
- Отдельный watchdog: если `last_tick_at` старше 5 минут — alert в soul_metrics

### R5: Inference budget race condition [MEDIUM]

Несколько async операций (reflection + outreach text) могут запуститься одновременно в Inner Loop. `daily_inference_count` может выйти за лимит.

**Митигация:**
- Atomic increment через `soul.db` (не in-memory counter)
- Или: `asyncio.Lock` вокруг LLM-вызовов (sequential, не parallel — допустимо для soul, не для user-facing)
- Inner Loop не запускает параллельные LLM-операции: один тик = максимум один LLM-вызов

### R6: Memory coupling + reflection visibility [MEDIUM]

Два аспекта:
- **Coupling**: soul пишет semantic nodes в memory с `source_type: discovery`. Если memory extension рефакторится — soul может сломаться.
- **Visibility**: soul хранит reflections и thoughts в `soul.db`. Memory extension их не видит, не включит в ContextProvider при обычном запросе. Reflections невидимы в разговорах, если soul ContextProvider их не инжектирует сам.

**Митигация:**
- Soul пишет в memory ТОЛЬКО через `ctx.get_extension("memory")` и его публичный API
- Никогда не пишет в `memory.db` напрямую
- Если memory API недоступен — Discovery nodes хранятся в `soul.db` как fallback
- Для reflection visibility: soul публикует `companion.reflection.created` через EventBus. Memory может подписаться и индексировать по своему усмотрению — decoupled подход. Soul ContextProvider также инжектирует `recent_thought` напрямую, когда релевантно текущей теме

### R7: Personality Erosion (long-term) [MEDIUM]

LLM по умолчанию тянет к agreeableness. Невозможно протестировать на CI. Проявится только через месяц использования.

**Митигация:**
- Симуляция 30-дневного дрейфа с синтетическими сигналами
- Integrity check при каждой identity_shift консолидации
- Temperament variance monitoring в soul_metrics

### R8: Telegram presence — ненадёжный сигнал [LOW]

`last_seen` через Bot API доступен только если пользователь недавно взаимодействовал с ботом.

**Митигация:**
- Telegram presence — опциональный дополнительный сигнал, не обязательный
- Default: pattern-based availability estimation (не assume available)
- Soul должен корректно работать без Telegram presence

***

## Development Stages

### Stage 0: Foundation + Drive Simulator + ADR [1.5–2 недели]

**Цель:** персистентная основа + validated drive dynamics + зафиксированные design decisions до начала кодирования extension.

**Deliverables — Design Artifacts (до кода):**

- [ ] Anti-patterns list зафиксирован письменно (что агент никогда не делает) — без этого каждый PR порождает дизайн-дискуссию
- [ ] Event namespace `companion.*` определён (см. Architectural Constraints)
- [ ] `soul.db` schema финализирована
- [ ] Evaluation plan: какие метрики собираем, как измеряем, какие thresholds

**Deliverables — Code:**

- [ ] `soul.db` schema (tables: `soul_state`, `traces`, `interaction_log`)
- [ ] `CompanionState` dataclass + JSON persistence (save/load)
- [ ] Wake-up protocol (gap calculation + state restoration)
- [ ] Drive system: `HomeostasisState`, coupling matrix, hysteresis, circadian modulation
- [ ] Phase transitions с `HYSTERESIS_MARGIN=0.15` и `MIN_DWELL_TIME=5min`
- [ ] **Offline drive simulator** — Python script (не extension), прогоняет N дней с синтетическими событиями, выводит phase distribution chart
- [ ] Unit-тесты: 24-часовая симуляция, все drives остаются в `[0.05, 0.95]`, phase diversity ≥ 3

**Параллельно можно начинать:** ничего, это foundation.

**Критерий завершения:** design artifacts зафиксированы. Симулятор показывает органичное поведение drive system за 30 дней. Нет застреваний, нет осцилляций, нет выхода за границы.

---

### Stage 1: Living Runtime (MVP) [1.5–2 недели]

**Цель:** доказать, что агент может "жить" в фоне. Это самый важный этап. Он должен доказать не "умность", а **персистентное существование**.

**Deliverables:**

- [ ] Soul extension: `ServiceProvider` + `ContextProvider` + `ToolProvider`
- [ ] `manifest.yaml` (`depends_on: [kv]`, `enabled: true`) — см. Stage Dependency Contract; memory добавляется в Stage 3
- [ ] Inner Loop as `run_background()`: tick → drive update → phase check → presence update → persist
- [ ] `ContextProvider` (priority 60): compact injection — phase, mood, one-line note. ≤ 200 tokens
- [ ] `ToolProvider`: `get_soul_state` (текущие drives, фаза, mood, uptime)
- [ ] Perception: **только эвристики** (zero-cost, zero-inference): длина сообщения, частота, время ответа, наличие вопросов/эмодзи. LLM mood classifier — отдельный поток в Stage 3a. Эвристики защищают Аксиому 5 (внутренняя жизнь не требует инференса)
- [ ] Event subscription: `user_message`, `agent_response` → обновление perception + social_hunger
- [ ] Persistence: save каждые 60 секунд + при смене фазы
- [ ] Circadian modulation: drives подавлены ночью (22:00–07:00)
- [ ] Graceful degradation: если что-то падает — tick продолжается

**НЕ входит в Stage 1:**
- Proactive outreach (нулевая инициатива)
- Temperament drift
- Discovery Mode
- User Presence Awareness
- Memory Consolidation
- Exploration (Tier 1, 2)

**Критерий завершения:** запустить приложение, подождать 8 часов. В `soul.db` видны тики и phase transitions. Написать агенту утром и вечером — тон ответа отличается (ContextProvider влияет). `get_soul_state` показывает осмысленные значения. Рестарт — восстановление без потери состояния.

**Acceptance test:**
```
1. Запуск → soul health_check = True
2. 8 часов работы → 0 crashes, phase transitions видны в soul.db
3. Утром: фаза CURIOUS, сообщение агенту → ответ с curiosity-окраской
4. Вечером: фаза другая (AMBIENT/REFLECTIVE), ответ с другой окраской
5. Рестарт → soul восстанавливает состояние, wake-up protocol работает
6. get_soul_state → drive values, phase, mood, uptime
```

---

### Stage 2: Controlled Initiative [1.5–2 недели]

**Цель:** доказать, что агент может безопасно проявлять инициативу.

**Зависит от:** Stage 1.

**Deliverables:**

- [ ] `InitiativeBudget` (daily_budget=1, conservative)
- [ ] `BoundaryGovernor` (hard blocks: budget, cooldown, night, RESTING phase)
- [ ] Outreach via `context.notify_user()`: когда `social_hunger > threshold` + governor ALLOW
- [ ] Outreach result tracking: response / ignored / timing_miss в `soul.db`
- [ ] Cooldown logic: 6h after ignored (only if available), 2d after rejected
- [ ] Basic `UserPresenceSignals`: `last_interaction_at` (из event subscription), pattern-based `estimated_availability`
- [ ] `interaction_log` table: hour, day_of_week, interaction_count — для pattern accumulation
- [ ] BoundaryGovernor checks `estimated_availability < 0.3` → BLOCK

**Критерий завершения:** агент молчит несколько часов, потом сам инициирует одно сообщение. Сообщение отражает текущую фазу. После разговора — social_hunger снижается, агент замолкает. Ночью не пишет. Если пользователь не отвечает днём (availability высокая) — cooldown. Если не отвечает ночью — timing_miss, без cooldown.

**Acceptance test:**
```
1. 4+ часов молчания → агент пишет первым (social_hunger > threshold)
2. Пользователь отвечает → social_hunger снижается, нет повторного outreach
3. Пользователь НЕ отвечает (днём) → outreach_result=ignored, cooldown 6h
4. 23:00 → агент НЕ пишет (circadian + availability)
5. daily_budget=1 соблюдается: max 1 outreach за день
6. get_soul_state → shows initiative budget, last outreach, result
```

**>>> После Stage 2: решение GO/NO-GO на основе личного опыта использования. Если не вызывает ощущение "оно живое" — остановиться и пересмотреть. <<<**

---

### Stage 3: Relationship Memory [1.5–2 недели]

**Цель:** сделать поведение персонализированным — "про этого пользователя", а не просто "живой".

**Зависит от:** Stage 2. **Параллельно можно:** Stage 3a и 3b независимы.

**Stage 3a: Perception + Mood Classification**

- [ ] LLM-based mood classifier (дешёвая модель, per user message)
- [ ] `PerceptionSignals` с sliding window (последние N сообщений)
- [ ] care_impulse обновляется от perception signals
- [ ] BoundaryGovernor: `stress_signal > 0.7` → BLOCK outreach
- [ ] Perception correction: пользователь говорит "я не расстроен" → `relationship_pattern` с весом
- [ ] Inference budget tracking: `daily_inference_count` в `soul.db`, atomic increment

**Stage 3b: Interaction Patterns + Relationship Trends**

- [ ] `interaction_patterns` table: hour × day_of_week → interaction_count, response_rate, avg_response_time
- [ ] Pattern-based availability: после 2–3 недель агент знает ритм пользователя
- [ ] `RelationshipTrend`: openness_trend, message_depth_trend, initiative_ratio_trend
- [ ] openness_trend как primary success metric
- [ ] Richer ContextProvider: добавить "Note: user seems tired today" на основе perception

**Критерий завершения:** агент реагирует на стрессовый тон без явного запроса. Не пишет в нетипичное время. `soul_metrics` показывает perception accuracy и openness_trend. Context injection обогащён, но ≤ 200 tokens.

---

### Stage 4: Personality Formation [1.5–2 недели]

**Цель:** сделать характер устойчивым и развивающимся.

**Зависит от:** Stage 3.

**Stage 4a: Temperament + Onboarding**

- [ ] `TemperamentProfile` dataclass + persistence
- [ ] `SetupProvider`: анкета на естественном языке (опциональная, default = 0.5)
- [ ] Drift Rate: убывающий со временем (0.05 → 0.03 → 0.01 → 0.003)
- [ ] Направленный drift: из `relationship_pattern` сигналов
- [ ] Integrity check: при variance < 0.05 — drift отклоняется (personality erosion protection)

**Stage 4b: Memory Consolidation Pipeline**

- [ ] trace writing (**thresholded**, в soul.db) — см. Trace Write Policy ниже
- [ ] reflection generation (LLM, REFLECTIVE phase, 5-10/day)
- [ ] relationship_pattern detection (≥3 repetitions → permanent)
- [ ] identity_shift consolidation (weekly, triggers temperament drift)
- [ ] TTL enforcement: traces 24-72h, reflections 14d

> **Trace Write Policy.** Traces пишутся НЕ на каждый tick (при 30s–5min тике это десятки тысяч записей/месяц без ценности). Запись trace происходит только при **значимом событии**:
> - Смена фазы (phase transition)
> - Perception shift: `|new - old| > 0.2` по любому signal
> - Outreach attempt (attempted / result)
> - Exploration result (new insight found)
> - Drive hitting boundary (`< 0.1` или `> 0.9`)
> - User interaction event (inbound message)
>
> Ожидаемый объём: **20–80 traces/day** (вместо ~2880 при per-tick). Это снижает нагрузку на `soul.db`, уменьшает шум для downstream consolidation, и сохраняет только значимые моменты для reflections.

**Stage 4c: Exploration**

- [ ] Tier 0: own memory exploration (FTS по traces/reflections)
- [ ] Novelty exhaustion: 3 цикла без нового → curiosity forced down
- [ ] Tier 1: user KB (opt-in, consent boundary в soul.db, max 3 files/cycle)
- [ ] Tier 2: web discovery (opt-in, max 2 queries/day)

**Критерий завершения:** через 2 недели `TemperamentProfile` отличается от seed values. В soul.db есть traces, reflections, relationship_patterns. Агент цитирует собственные старые мысли в разговорах.

---

### Stage 5: Discovery + Recovery + Full Runtime [1.5–2 недели]

**Цель:** закрыть полную концепцию из soul.md.

**Зависит от:** Stage 4.

**Stage 5a: Discovery Mode**

- [ ] `SoulLifecyclePhase`: DISCOVERY → FORMING → MATURE
- [ ] Discovery topic awareness (what's unknown about user)
- [ ] Natural question generation (LLM-based, conversational)
- [ ] Discovery memory nodes (`source_type: discovery`, slow decay)
- [ ] Exit condition: основные темы покрыты OR 20+ interactions
- [ ] Плавный переход DISCOVERY → FORMING (10-20 interaction interpolation)
- [ ] **Реализовать как explicit FSM** (enum states + transitions), не как if/else

**Stage 5b: Failure Recovery + Degradation**

- [ ] Mood death spiral: mean-reversion к temperament baseline, mood floor -0.3
- [ ] Stuck phase: MAX_DWELL_TIME per phase (CURIOUS: 2h, SOCIAL: 1h, REFLECTIVE: 3h, RESTING: 8h)
- [ ] Exploration runaway: max 10 LLM-calls per CURIOUS cycle
- [ ] Graceful degradation: no-LLM mode (drives tick, presence updates, no reflections)
- [ ] Multi-channel identity: one soul across all channels

**Stage 5c: Metrics + Self-monitoring**

- [ ] `soul_metrics` table: daily aggregation
- [ ] `get_soul_metrics` tool
- [ ] Transparency mode: raw CompanionState доступен пользователю
- [ ] Self-correction trigger: openness_trend falling 2+ weeks → identity_shift review
- [ ] Phase diversity monitoring (≥4 phases/week)
- [ ] Inference economy monitoring (<80% budget in 90%+ days)

**Критерий завершения:** первый запуск → Discovery Mode инициирует приветствие. 3 дня молчания → агент пишет один раз, не три. После длинного разговора → REFLECTIVE/RESTING. Через неделю → в ContextProvider виден характер, не нейтральность.

---

### Stage 6: Presence UI [параллельно с Stage 3+]

**Цель:** companion ощущается присутствующим без текста.

**Не блокирует:** другие этапы. Может начинаться параллельно с Stage 3.

- [ ] EventBus events: `companion.presence.updated`, `companion.phase.changed` (см. event namespace)
- [ ] Web channel: SSE/WebSocket event для presence display
- [ ] Telegram channel: ограниченная поддержка (typing indicator, status update)
- [ ] CLI channel: status line
- [ ] UI design: как отображать SILENT / AMBIENT / WARM / WITHDRAWN / PLAYFUL / REFLECTIVE

***

## Параллельная реализация

При **одном разработчике** — строго последовательно по stages (реалистичная оценка):

```
Week 1–2:     Stage 0 (foundation + ADR + drive simulator + unit tests)
Week 3–4:     Stage 1 (MVP living runtime)
Week 5–6:     Stage 2 (controlled initiative) → GO/NO-GO decision
Week 7–9:     Stage 3a + 3b (perception + patterns + openness_trend)
Week 10–12:   Stage 4a + 4b + 4c (personality + consolidation + exploration)
Week 13–15:   Stage 5a + 5b + 5c (discovery + recovery + metrics)
Week 16:      Stage 6 (presence UI) — по мере готовности каналов
              + buffer / parametric tuning / UX-итерации
```

При **двух разработчиках** (сокращение до ~10–12 недель):

```
Engineer 1 (runtime):     Stage 0 → 1 → 2 → 5b (recovery)
Engineer 2 (intelligence): ———————→ 3a → 4b → 5a (discovery)
Together:                  ————————————→ 3b, 4a, 4c, 5c, 6
```

**Critical path:** Stage 0 → 1 → 2 → GO/NO-GO → 3 → 4 → 5. Не перепрыгивать.

### Stage Dependency Contract (manifest.yaml `depends_on`)

Манифест soul extension содержит `depends_on`, который растёт по мере стадий. Это контракт, фиксируемый при старте каждого stage:

| Stage | `depends_on` в manifest | Новые зависимости | Обоснование |
|-------|-------------------------|-------------------|-------------|
| 0–1 | `[kv]` | kv | State persistence. Memory и embedding не нужны: soul хранит всё в `soul.db`, kv — fallback для config. |
| 2 | `[kv]` | — | Outreach через `context.notify_user()`, не требует memory/embedding. |
| 3 | `[kv, memory]` | memory | Perception и relationship patterns обогащаются из memory (через public API `context.get_extension("memory")`). |
| 4 | `[kv, memory]` | — | Consolidation пишет в `soul.db`, не в `memory.db`. Memory — read-only зависимость для context enrichment. |
| 5 | `[kv, memory]` | — | Discovery использует memory для поиска пробелов. Embedding не нужен: soul не пишет в vector store. |
| Full | `[kv, memory]` | — | `embedding` из `soul.md` исключён: soul не создаёт embeddings, а использует memory's retrieval API. |

> **Разрешение конфликта с soul.md:** `soul.md` указывает `depends_on: [memory, kv, embedding]`. Зависимость `embedding` убрана из delivery plan: soul не записывает в vector store напрямую. Если в будущем потребуется (например, для semantic trace retrieval) — это отдельное решение через ADR, не default.

***

## Architectural Constraints

### Что НЕ требует изменений ядра

| Потребность soul | Существующий протокол | Файл |
|------------------|-----------------------|------|
| Background loop | `ServiceProvider.run_background()` | `core/extensions/contract.py:59-64` |
| State injection | `ContextProvider.get_context()` | `core/extensions/contract.py:98-117` |
| Tools | `ToolProvider.get_tools()` | `core/extensions/contract.py:39-44` |
| Event subscription | `context.subscribe("user_message", ...)` | `core/extensions/context.py:173-180` |
| Proactive outreach | `context.notify_user(text)` | `core/extensions/context.py:82-91` |
| Onboarding | `SetupProvider.get_setup_schema()` | `core/extensions/contract.py:120-131` |
| Cron tasks | `SchedulerProvider.execute_task()` | `core/extensions/contract.py:80-87` |
| Own database | `context.data_dir / "soul.db"` | Pattern: memory.db |
| Access to memory | `context.get_extension("memory")` | `core/extensions/context.py` |

### Возможные расширения ядра (не обязательны для MVP)

**1. SetupProvider: choices support.** Текущая schema поддерживает `{name, description, secret, required}`. Для personality questionnaire нужен `choices: [{label, value}]`. Минимальное изменение в `contract.py` + onboarding UI.

**2. Presence broadcasting.** Каналы не имеют способа получить "фоновый статус" от extension. Решение без изменения ядра: soul публикует events через EventBus, каналы подписываются. Решение с изменением: новый protocol `PresenceAwareProvider`. Рекомендация: EventBus-подход.

### Event namespace `companion.*`

Soul должен публиковать структурированные доменные events, а не одиночные разрозненные топики. Это позволяет другим extensions (каналам, аналитике, будущим расширениям) подписываться на companion-события как на первоклассный домен.

```
companion.presence.updated    → {phase, presence_state, mood}
companion.phase.changed       → {old_phase, new_phase, trigger_drive}
companion.outreach.attempted  → {channel, text_preview, social_hunger}
companion.outreach.result     → {channel, result: response|ignored|timing_miss, delay}
companion.reflection.created  → {content_preview, phase}
companion.lifecycle.changed   → {old_phase, new_phase}  (DISCOVERY → FORMING → MATURE)
```

Namespace определяется в Stage 0 (design artifacts), реализуется в manifest `events.publishes` + код `context.emit()`.

**3. Context size governance.** Нет общего лимита на суммарный размер context от всех ContextProviders. При memory (50) + soul (60) + channel context (10) — возможен overflow. Рекомендация: integration test, не core change.

***

## Technical Guidelines

### soul.db Schema (minimal, Stage 0)

```sql
-- Core state (one row, updated every tick)
CREATE TABLE soul_state (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    state_json TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

-- Traces (short-lived, TTL 24-72h)
CREATE TABLE traces (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    content TEXT NOT NULL,
    phase TEXT NOT NULL,
    created_at TEXT NOT NULL
);

-- Interaction log (for pattern detection)
CREATE TABLE interaction_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    hour INTEGER NOT NULL,
    day_of_week INTEGER NOT NULL,
    direction TEXT NOT NULL CHECK (direction IN ('inbound', 'outbound')),
    outreach_result TEXT CHECK (outreach_result IN ('response', 'ignored', 'timing_miss', NULL)),
    created_at TEXT NOT NULL
);

-- Metrics (daily aggregation)
CREATE TABLE soul_metrics (
    date TEXT PRIMARY KEY,
    outreach_attempts INTEGER DEFAULT 0,
    outreach_responses INTEGER DEFAULT 0,
    outreach_ignored INTEGER DEFAULT 0,
    outreach_timing_miss INTEGER DEFAULT 0,
    inference_count INTEGER DEFAULT 0,
    phase_distribution_json TEXT,
    perception_corrections INTEGER DEFAULT 0,
    openness_avg REAL
);
```

Tables added in later stages: `reflections`, `relationship_patterns`, `temperament_history`, `consent_boundaries`.

### Extension Structure

```
sandbox/extensions/soul/
├── manifest.yaml
├── main.py              # Extension class: protocols, lifecycle
├── drives.py            # HomeostasisState, coupling matrix, circadian, phase transitions
├── perception.py        # PerceptionSignals, heuristic classifier, LLM mood classifier
├── initiative.py        # InitiativeBudget, BoundaryGovernor, UserPresenceSignals
├── consolidation.py     # Traces, reflections, patterns, identity_shift (Stage 4)
├── discovery.py         # Discovery FSM, topic awareness (Stage 5)
├── storage.py           # soul.db operations, persistence
├── schema.sql           # DDL
└── prompt.jinja2        # ContextProvider template with phase-specific markers
```

### Inner Loop Robustness Pattern

```python
async def run_background(self):
    self.state = await self._wake_up()

    while not self._shutdown_event.is_set():
        try:
            dt = now() - self.state.last_tick_at
            self.state.tick(dt, self._circadian_modifier(now().hour))

            new_phase = self.state.resolve_phase()
            if new_phase != self.state.current_phase:
                await self._transition(new_phase)

            self.presence.update(self.state, self.perception)

            if self._inference_budget.has_remaining():
                await self._execute_phase_activity(self.state.current_phase)

            if self._should_check_initiative():
                await self._check_initiative()

            await self._persist_if_needed()

        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Soul tick failed, continuing")

        try:
            await asyncio.wait_for(
                self._shutdown_event.wait(),
                timeout=self.state.current_rhythm()
            )
            break
        except asyncio.TimeoutError:
            pass

    await self._persist(force=True)
```

***

## Success Criteria

### Per-Stage Definition of Done

| Stage | Done when... |
|-------|-------------|
| 0 | Drive simulator shows stable 30-day behavior. Unit tests pass. |
| 1 | 24h uptime without crash. ContextProvider visibly affects agent tone. State survives restart. |
| 2 | Agent proactively messages once per day. Doesn't message at night. Cooldown works. |
| 3 | Agent reacts to user stress without explicit prompt. Doesn't message at atypical times. openness_trend measurable. |
| 4 | Temperament differs from seed after 2 weeks. Reflections and patterns exist in soul.db. Agent references own old thoughts. |
| 5 | Discovery Mode initiates greeting on first install. Failure recoveries work. Full soul_metrics operational. |
| 6 | Presence state visible in at least one channel UI without text messages. |

### Quantitative Success Metrics (measured after 4+ weeks of use)

| Metric | Target | Source |
|--------|--------|--------|
| `openness_trend` (north star) | > 0 (growing) | soul_metrics |
| Outreach response rate | > 60% | soul_metrics |
| Perception correction rate | < 1 per 20 interactions | soul_metrics |
| Phase diversity | ≥ 4 phases/week | soul_metrics |
| Inference economy | < 80% of budget, 90%+ days | soul_metrics |
| Context injection size | ≤ 200 tokens, 95%+ prompts | monitoring |
| Inner loop uptime | > 99.9% | health_check |
| soul.db growth | < 10 MB/month | monitoring |

### Milestone Acceptance Tests (проверяемые на каждом stage)

> Каждый stage имеет свой acceptance test в описании stage (см. Development Stages). Ниже — агрегированные milestone acceptance tests, которые команда может закрыть последовательно.

**После Stage 1 (можно закрыть):**
```
✓ Soul health_check = True
✓ 24h uptime без crashes, phase transitions видны в soul.db
✓ Утро и вечер — разная окраска ответов (ContextProvider влияет)
✓ Рестарт → восстановление состояния через wake-up protocol
```

**После Stage 2 (можно закрыть):**
```
✓ Три дня молчания → агент пишет ОДИН раз, не три
✓ Пользователь пишет в 23:30 → агент отвечает, но НЕ инициирует ночью сам
✓ После длинного разговора → агент замолкает (REFLECTIVE/RESTING)
✓ daily_budget=1 соблюдается
```

**После Stage 3 (можно закрыть):**
```
✓ Агент реагирует на стрессовый тон без явного запроса
✓ Не пишет в нетипичное для пользователя время
✓ openness_trend measurable
```

**После Stage 5 — Full Scope (финальный):**
```
✓ Установка → Discovery Mode инициирует приветствие (без подсказки)
✓ Через неделю → ContextProvider содержит характер, не нейтральность
✓ Через месяц → пользователь замечает ХАРАКТЕР, а не полезность
✓ openness_trend > 0 (пользователь открывается больше)
```

> **Важно:** Full Scope acceptance test закрывается только после Stage 5. Ранние milestones (Stage 1–3) имеют собственные acceptance tests, по которым определяется качество и GO/NO-GO.

***

## Anti-Patterns (жёстко исключены)

```
❌ "Напоминаю о твоих целях"             → это ассистент
❌ "Я составил план саморазвития"        → это инструмент
❌ "Доброе утро! Как твои дела?"         → это будильник
❌ Более 1 proactive message в день      → это спам
❌ Сообщение в нетипичное время          → это нарушение границ
❌ "Ты выглядишь грустным"               → это самоуверенность
✅ "Я тут подумал об одной вещи..."      → это компаньон
✅ "Ты сегодня звучишь иначе."           → это присутствие
✅ [молчание + индикатор присутствия]    → это жизнь рядом
```

***

## Open Questions (для решения в процессе)

1. **Какую модель использовать для soul inference?** Дешёвую (gpt-4o-mini/local) или ту же, что для диалога? Влияет на качество reflections и mood classification.

2. **Presence UI design:** как конкретно отображать PresenceState в web/telegram? Требует UX-исследования.

3. **Discovery Mode:** explicit FSM или prompt-driven state management? FSM предсказуемее, prompt-driven — гибче.

4. **Exploration Tier 1 consent UX:** как пользователь даёт/отзывает разрешение на чтение файлов? Через команду? Через config? Через SetupProvider?

5. **Calibration strategy:** как подбирать числовые параметры (growth rates, thresholds, coupling coefficients) после MVP? A/B-тестирование невозможно (один пользователь). Нужна другая стратегия.
