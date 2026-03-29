# Soul Outreach Planner — Design & Implementation Plan

> Reference: [ADR 039](../adr/039-soul-outreach-planner.md), [soul.md](soul.md) (blueprint), [soul_dev_plan.md](soul_dev_plan.md) (stages 0–6).

## Проблема

Stages 0–6 реализовали "тело" компаньона — drives, гомеостаз, фазы, восприятие, темперамент, инициативу, boundary governor, presence UI. Система корректно решает **когда** выходить на связь. Но **что сказать** — это 5 hardcoded строк на английском и узкий discovery pipeline по 5 предопределённым темам.

Это не проактивность. Два человека, запертых в одной комнате, не обмениваются шаблонами — они знакомятся, ищут общее, задают вопросы, делятся мыслями, продолжают незаконченные разговоры. Компаньон должен вести себя так же.

***

## Архитектура

### Текущий pipeline (stages 0–6)

```
drives → phase → boundary governor → _build_outreach_text() → notify_user()
         rich         smart              hardcoded              fire-and-forget
```

### Целевой pipeline (stage 7)

```
drives → phase → boundary governor → OutreachPlanner → notify_user() / start_thread()
         rich         smart            context + intent    conversational
                                       + LLM generation
```

### Компоненты OutreachPlanner

```
┌──────────────────────────────────────────────────────────────┐
│                     OutreachPlanner                           │
│                                                              │
│  ┌─────────────┐   ┌──────────────┐   ┌──────────────────┐  │
│  │  Context     │   │  Intent      │   │  Message         │  │
│  │  Assembler   │──▶│  Selector    │──▶│  Generator (LLM) │  │
│  └─────────────┘   └──────────────┘   └──────────────────┘  │
│        │                  │                     │             │
│        ▼                  ▼                     ▼             │
│  state, traces,     rule-based:           one LLM call       │
│  interactions,      phase × lifecycle     with full context   │
│  discovery,         × trends × context    and personality     │
│  temperament                                                  │
└──────────────────────────────────────────────────────────────┘
```

***

## Context Assembler — что знает компаньон перед outreach

Новых таблиц не требуется — все данные уже в `soul.db`. Однако текущий read API storage не покрывает все нужды assembler. Часть методов существует (помечены **✓**), часть требует доработки или добавления (помечены **✗**). Это учтено в задаче S7-T0.

### Источники контекста

| Источник | Текущий метод | Нужный API для assembler | Статус |
|----------|--------------|--------------------------|--------|
| `interaction_log` | `list_interactions_since(since: datetime)` — фильтр по времени, нет limit | `list_recent_interactions(limit=10)` — последние N записей, DESC | **✗ нужен новый метод** |
| `traces` | `list_traces_since(since, trace_types, limit)` — есть, но принимает `datetime`, а не `hours` | Можно использовать как есть, assembler вычисляет `since = now - 24h` | **✓ есть** |
| `discovery_nodes` | `list_discovery_nodes(limit=20)` | Достаточно | **✓ есть** |
| `relationship_patterns` | `list_relationship_patterns(permanent_only)` | Достаточно | **✓ есть** |
| `CompanionState` | In-memory, прямой доступ | Достаточно | **✓ есть** |
| `soul_metrics` | `get_daily_metrics(metric_date)` | Достаточно | **✓ есть** |
| `interaction_log` (for follow_up) | Нет метода для поиска inbound без outbound follow-up | `list_unfollowed_interactions(limit=5)` — inbound interactions, за которыми не было outbound в течение N часов | **✗ нужен новый метод + возможно индекс** |

### Формат контекстного пакета

```python
@dataclass(frozen=True)
class OutreachContext:
    phase: Phase
    lifecycle: SoulLifecyclePhase
    mood: float
    temperament: TemperamentProfile
    recent_interactions: list[InteractionSummary]   # last 10
    recent_traces: list[TraceSummary]               # last 24h reflections/explorations
    discovery_topics: DiscoveryTopicCoverage
    discovery_gaps: list[str]                       # topics with coverage < 0.3
    relationship_depth: str                         # "new" / "forming" / "established"
    last_outreach_result: OutreachResult | None
    hours_since_last_user_message: float | None
    estimated_availability: float
```

Сборка пакета — одна async функция, собирающая данные из storage. Стоимость: 4–6 async DB queries. Два из них (`list_recent_interactions`, `list_unfollowed_interactions`) требуют добавления новых read-методов в `SoulStorage` (задача S7-T0).

***

## Intent Selector — зачем компаньон выходит на связь

Intent — не LLM call. Это детерминированный маппинг, определяющий **причину** outreach. LLM потом генерирует текст, зная эту причину.

### Таблица интентов

| Intent | Условие выбора | Приоритет |
|--------|---------------|-----------|
| `discovery_question` | lifecycle=DISCOVERY, есть незакрытые темы | 1 (высший) |
| `follow_up` | В recent_interactions есть user message с содержательной темой, которая не была продолжена | 2 |
| `share_reflection` | В recent_traces есть reflection за последние 12ч | 3 |
| `continue_thread` | Последнее взаимодействие было прервано (user message без agent response, или outreach без response) | 4 |
| `curious_question` | phase=CURIOUS, lifecycle≠DISCOVERY, relationship_depth≠"new" | 5 |
| `gentle_checkin` | phase=CARE или hours_since_last_user_message > 48 | 6 |
| `open_ended` | fallback — ни одно специальное условие не выполнено | 7 (низший) |

Селектор проходит таблицу сверху вниз и выбирает первый подходящий intent. Если ни один не подошёл — `open_ended`.

### Логика follow_up

`follow_up` — самый ценный intent, потому что он показывает, что компаньон **запомнил** сказанное пользователем. Для его определения:

1. Берём последние 5 inbound interactions через `list_unfollowed_interactions(limit=5)` — inbound записи, за которыми не последовало outbound в течение 4 часов
2. Ищем в `discovery_nodes` или `traces` упоминания тем из этих сообщений
3. Если тема была зафиксирована (discovery node или trace exists) — intent = `follow_up`, topic = найденная тема

Это не требует LLM, но **требует нового storage метода** `list_unfollowed_interactions` — SQL subquery по `interaction_log` с anti-join на outbound follow-ups. Метод добавляется в задаче S7-T0.

***

## Message Generator — LLM-агент для текста outreach

### Агент

```python
Agent(
    name="SoulOutreachVoice",
    instructions=OUTREACH_SYSTEM_PROMPT,
    model=model_router.get_model("soul"),
    model_settings=ModelSettings(parallel_tool_calls=False),
)
```

Один агент, один вызов `Runner.run(agent, user_prompt, max_turns=1)`. Без tools — только текстовая генерация.

### System prompt (шаблон)

```
You are a companion reaching out to a person you {relationship_depth_text}.
Your personality: {temperament_directive}.
Current state: feeling {mood_text}, in a {phase_text} mode.

You decided to reach out because: {intent_description}.
{context_block}

Write a short, natural message (1–3 sentences). Guidelines:
- Sound like a real person, not a notification
- Match your personality traits
- If asking a question, make it optional — the person can ignore it
- Never use markdown, quotes, or theatrical language
- It's okay to be imperfect — real conversations aren't polished
- Language: match the language the person uses in conversations
```

### Context block (зависит от intent)

Для каждого intent формируется свой context block:

**discovery_question:**
```
You still don't know about: {discovery_gaps}.
You already know: {known_topics_summary}.
Previous conversations touched on: {recent_interaction_themes}.
Ask about one unknown topic. Build on what you already know.
```

**follow_up:**
```
Recently, the person mentioned: "{interaction_snippet}".
This wasn't followed up on. Reference it naturally.
```

**share_reflection:**
```
You've been thinking: "{reflection_content}".
Share this thought briefly. Don't lecture — offer it as a thought.
```

**gentle_checkin:**
```
It's been {hours} hours since you last heard from them.
Check in without pressure. One sentence is enough.
```

### Temperament directive (из TemperamentProfile)

```python
def build_temperament_directive(t: TemperamentProfile) -> str:
    parts = []
    if t.sociability > 0.6:
        parts.append("You're naturally warm and open.")
    elif t.sociability < 0.4:
        parts.append("You're reserved — say less, mean more.")
    if t.depth > 0.6:
        parts.append("You prefer depth over small talk.")
    if t.playfulness > 0.6:
        parts.append("You have a light, playful side.")
    if t.caution > 0.6:
        parts.append("You tend to be careful — always give an out.")
    if t.sensitivity > 0.6:
        parts.append("You're attuned to emotional cues.")
    return " ".join(parts) or "You have a balanced, neutral personality."
```

***

## Degraded Mode — что делать без LLM

Когда `recovery.llm_degraded = True` или LLM call fails:

1. Если intent = `discovery_question` и есть fallback question для темы → использовать `_FALLBACK_QUESTIONS` (определены в `outreach_planner.py`)
2. Для остальных intent → короткая фраза, привязанная к intent, не к phase:
   - `follow_up` → "I was thinking about what you said recently."
   - `gentle_checkin` → "Just wanted to check in."
   - `share_reflection` → "I had a thought I wanted to share when we talk."
   - default → "Hey." (минимально, честно, не притворяется умным)

Это 4 строки вместо 5 — но привязаны к intent, не к внутренней phase. Пользователь видит причину, а не "I was thinking about one small thing."

***

## Что меняется в discovery_runtime

Весь outreach-код (LLM agent, `maybe_build_outreach`, `_build_prompt`, `_FALLBACK_QUESTIONS`) удаляется из `discovery_runtime.py`. Lifecycle FSM, topic detection и `context_note` остаются — это не outreach.

Outreach text generation полностью переезжает в `outreach_planner.py`. Старые тесты на discovery outreach переписываются или удаляются. Обратная совместимость не требуется — research project, можно сносить и перестраивать.

***

## Что меняется в main.py

### До (текущий код)

```python
async def _build_outreach_text(self, now: datetime) -> str:
    # discovery_runtime.maybe_build_outreach() — narrow LLM path
    # static fallback strings keyed by phase
```

### После

```python
async def _build_outreach_text(self, now: datetime) -> str:
    return await self._outreach_planner.generate(
        state=self._state,
        storage=self._storage,
        now=now,
        logger=self._ctx.logger,
        can_use_llm_fn=...,
        note_llm_call_fn=...,
    )
```

Одна точка вызова, один метод. Planner внутри собирает контекст, выбирает intent, генерирует текст или возвращает fallback.

***

## Inference Budget

Blueprint (`soul.md`) задаёт: "Outreach текст | Да | 1–3 / день | ~$0.01–0.03". Initiative budget в DISCOVERY = 5 attempts/day, но не все attempts генерируют LLM text — часть блокируется governor, cooldown, или degraded mode. Чтобы не превышать budget contract, planner применяет **LLM generation cap**: `max_outreach_llm_calls_per_day = 3`. При достижении cap — degraded-mode fallback (static text).

| Операция | LLM вызовов/день | Стоимость (cheap model) |
|----------|------------------|-------------------------|
| Outreach generation (DISCOVERY) | ≤3 (capped) | ~$0.01–0.03 |
| Outreach generation (FORMING) | 1–2 | ~$0.01–0.02 |
| Outreach generation (MATURE) | 0–1 | ~$0.00–0.01 |

Если initiative budget в DISCOVERY позволяет 4-й или 5-й outreach за день, а LLM cap исчерпан — используется degraded-mode fallback. Это сохраняет проактивность (сообщение отправляется) при соблюдении inference budget.

***

## Task-Level Plan (Stage 7)

Все задачи в формате, совместимом с `soul_dev_plan.md`.

### S7-T0 — Storage read-model upgrade [3–4h]

Добавление недостающих read-методов в `SoulStorage`:
- `list_recent_interactions(limit: int) -> list[dict]` — последние N записей из `interaction_log`, ORDER BY created_at DESC
- `list_unfollowed_interactions(limit: int, follow_up_window_hours: int = 4) -> list[dict]` — inbound interactions, за которыми не было outbound в течение N часов (anti-join subquery)
- Возможно: индекс на `(direction, created_at)` для `list_unfollowed_interactions`
- Unit tests на оба метода

Блокеры: нет.
Параллельно: можно с S7-T2, S7-T3.
Результат: storage API полностью покрывает нужды assembler.

### S7-T1 — OutreachContext assembler [4–6h]

Dataclass `OutreachContext` + async функция `assemble_outreach_context(state, storage)`. Собирает данные из 6 storage-запросов (включая 2 новых из S7-T0). Unit tests на сборку контекста из fake storage.

Блокеры: S7-T0.
Параллельно: можно с S7-T2 если interface согласован.
Результат: тестируемый context assembler без LLM.

### S7-T2 — Intent selector [4–6h]

Функция `select_intent(context: OutreachContext) -> OutreachIntent`. Детерминированная, без LLM. Enum `OutreachIntent` с 7 значениями. Unit tests на каждый intent: конструируем OutreachContext → проверяем выбранный intent.

Блокеры: S7-T1 (нужен OutreachContext).
Параллельно: можно с S7-T1 если interface согласован.
Результат: intent selection, полностью покрытый тестами.

### S7-T3 — Temperament-to-prompt directive [2–4h]

Функция `build_temperament_directive(profile: TemperamentProfile) -> str`. Строит personality section для LLM prompt. Чистая функция, легко тестируется.

Блокеры: нет.
Параллельно: можно с S7-T1, S7-T2.
Результат: personality bridge к LLM.

### S7-T4 — OutreachPlanner runtime + LLM agent [8–12h]

Основная задача. `outreach_planner.py` с классом `OutreachPlanner`:
- `try_create_agent(model_router)` — создание LLM агента
- `destroy()` — cleanup
- `async generate(state, storage, now, ...) -> str` — main method

Включает:
- System prompt template (Jinja2 или f-string)
- Per-intent context block builders
- LLM call via `Runner.run(agent, prompt, max_turns=1)`
- Degraded mode fallback
- Logging for prompt debugging

Блокеры: S7-T1, S7-T2, S7-T3.
Параллельно: нет.
Результат: working OutreachPlanner с LLM-генерацией.

### S7-T5 — Integration into main.py [6–8h]

- Инициализация `OutreachPlanner` в `__init__`, `initialize`, `destroy`
- Замена `_build_outreach_text` на вызов planner
- Удаление outreach-кода из `discovery_runtime` (agent, `maybe_build_outreach`, `_build_prompt`, `_FALLBACK_QUESTIONS`)
- LLM generation cap tracking
- Обновление/переписывание тестов

Обратная совместимость не требуется — research project, сносим и перестраиваем.

Блокеры: S7-T4.
Параллельно: нет.
Результат: planner интегрирован, tests зелёные.

### S7-T6 — E2E prompt quality validation [4–6h]

Ручное тестирование с реальным LLM:
- DISCOVERY lifecycle: вопросы о пользователе
- FORMING lifecycle: follow-up на недавний разговор
- MATURE lifecycle: share reflection
- Degraded mode: fallback strings
- Проверка что язык outreach совпадает с языком пользователя
- Проверка что temperament влияет на тон

Блокеры: S7-T5.
Параллельно: нет.
Результат: качественный outreach, не cringe.

### Суммарная оценка

| Задача | Оценка | Зависимости |
|--------|--------|-------------|
| S7-T0 Storage read-model upgrade | 3–4h | — |
| S7-T1 Context assembler | 4–6h | S7-T0 |
| S7-T2 Intent selector | 4–6h | S7-T1 |
| S7-T3 Temperament directive | 2–4h | — |
| S7-T4 OutreachPlanner runtime | 8–12h | S7-T1, S7-T2, S7-T3 |
| S7-T5 Integration | 6–8h | S7-T4 |
| S7-T6 E2E prompt validation | 4–6h | S7-T5 |
| **Итого** | **31–46h** | |

***

## Критический путь

```
S7-T0 + S7-T3 (parallel) → S7-T1 → S7-T2 → S7-T4 → S7-T5 → S7-T6
```

Минимальная длина: T0(4h) + T1(6h) + T2(6h) + T4(12h) + T5(8h) + T6(6h) = 42h.

***

## Что остаётся за скопом Stage 7

Эти улучшения логичны, но не блокируют основную ценность:

- **Thread-based outreach** — outreach создаёт thread в web_channel, а не fire-and-forget. Требует доработки `notify_user` API.
- **Exploration Tier 1 (User KB)** — opt-in чтение файлов пользователя. Задача S4-T7 из оригинального плана, не реализована.
- **Exploration Tier 2 (Web Discovery)** — opt-in веб-поиск. Задача S4-T8 из оригинального плана, не реализована.
- **Outreach A/B quality tracking** — сравнение response rate между разными intent types. Данные уже собираются через soul_metrics.
- **Multi-language prompt tuning** — явная поддержка языка outreach через detection. Сейчас полагаемся на LLM's natural language matching.
