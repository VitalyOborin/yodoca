# Научное исследование: Архитектура SOTA AI-агентов и адаптация для Yodoca

## Executive Summary

Текущая архитектура Yodoca — nano-kernel с единственным Orchestrator-агентом, который получает все инструменты и extensions как плоский список — соответствует модели "Single Orchestrator + Tools". Исследование SOTA-подходов 2025–2026 года выявляет три трансформационных направления: (1) **динамическое создание sub-агентов** вместо статических ролей (AOrchestra), (2) **иерархическая оркестрация** по паттерну Anthropic orchestrator-worker, (3) **продвинутая память** на основе temporal knowledge graph (Zep/Letta). Адаптация потребует пересмотра ядра, но архитектурный фундамент Yodoca (EventBus, Extension protocols, декларативные агенты) создаёт отличную базу для эволюции.

***

## Часть 1: Анализ текущей архитектуры Yodoca

### Текущее состояние ядра

Yodoca построена как «all-is-extension» runtime с nano-kernel: Loader, EventBus, MessageRouter, ModelRouter, Orchestrator. Ядро не зависит от extensions — обнаружение capabilities происходит через `isinstance(ext, Protocol)`. Orchestrator — единственный Agent, который получает все tools от extensions в плоском списке.

### Выявленные ограничения

| Проблема | Описание | Влияние |
|----------|----------|---------|
| **Плоская tool-модель** | Все tools всех extensions загружаются в один Agent | Context pollution, LLM теряет фокус при >20 tools |
| **Статичные AgentProvider** | Декларативные агенты создаются при загрузке, не runtime | Нет адаптации к задаче |
| **Отсутствие context curation** | Нет фильтрации контекста для sub-агентов | Context rot при длинных сессиях |
| **Единый Orchestrator** | Один агент для всех задач | Нет специализации, bottleneck |
| **Memory как extension** | Память — обычный ContextProvider | Нет temporal reasoning, нет self-editing memory |

### Сильные стороны (что сохраняем)

- **EventBus с durable journal** — SQLite event journal с подписками, retry, recovery
- **Protocol-based contracts** — runtime checkable protocols для ToolProvider, ChannelProvider, AgentProvider, ContextProvider
- **Extension isolation** — core не зависит от extensions
- **ModelRouter** — provider-agnostic маршрутизация моделей

***

## Часть 2: SOTA-исследования и фреймворки

### 2.1 AOrchestra: Динамическое создание sub-агентов (ICLR 2025)

**Ключевая идея:** Любой агент моделируется как четвёрка `⟨I, C, T, M⟩` — Instruction, Context, Tools, Model. Оркестратор создаёт sub-агентов на лету, конкретизируя эту четвёрку для каждой подзадачи.[^1][^2]

**Архитектурные принципы:**
- Оркестратор **никогда не выполняет задачи сам** — только Delegate(Φ) и Finish(y)[^1]
- Каждый sub-агент получает **курированный контекст** — только релевантная информация, без полного дампа истории[^1]
- Sub-агенты **plug-and-play** — оркестратор агностичен к их реализации[^3]
- Оркестрация **обучаема**: SFT (+11.51% на GAIA) и in-context learning (−18.5% cost)[^1]

**Результаты:** 16.28% улучшение над сильнейшим baseline на трёх бенчмарках (GAIA, SWE-Bench, Terminal-Bench).[^3]

**Критический инсайт для Yodoca:** Текущий `AgentProvider` уже близок к паттерну sub-agent-as-tool, но ему не хватает dynamic context curation и runtime specialization. Четвёрка `⟨I, C, T, M⟩` — это естественная эволюция `AgentDescriptor`.

### 2.2 Anthropic Multi-Agent Research System (2025)

**Паттерн:** Orchestrator-worker с lead agent и специализированными subagents.[^4]

**Ключевые уроки:**
- Multi-agent с Claude Opus 4 (lead) + Sonnet 4 (workers) **превосходит single-agent на 90.2%** по internal eval[^4]
- **Teach the orchestrator to delegate** — каждый subagent получает: objective, output format, guidance on tools, clear task boundaries[^4]
- **Extended thinking** как controllable scratchpad — lead agent планирует подход, определяет сложность запроса, количество subagents и роль каждого[^4]
- **Let agents improve themselves** — Claude 4 модели способны быть prompt engineers; tool-testing agent переписал описания tools, что снизило время выполнения на 40%[^4]
- Token usage объясняет 80% дисперсии производительности; архитектура потребляет ~15× больше токенов чем обычный чат[^5]

**Критический инсайт для Yodoca:** Orchestrator должен уметь планировать количество и специализацию sub-агентов на основе анализа сложности запроса. Это отсутствует в текущей архитектуре.

### 2.3 Symphony: Иерархическое создание sub-агентов (2025)

Symphony предлагает пятикомпонентную архитектуру: Decomposition → Creating sub-agents → Planning → Orchestration → Integration. Каждый sub-агент **автономно создаёт свой system prompt** на основе контекста от оркестратора, что обеспечивает оптимальную специализацию.[^6]

### 2.4 OpenAI Agents SDK: Handoffs vs Agent-as-Tool

OpenAI Agents SDK (на котором построена Yodoca) поддерживает два паттерна:[^7]

| Паттерн | Handoff | Agent-as-Tool |
|---------|---------|---------------|
| Передача контроля | Полная — вся беседа переходит к новому агенту[^8] | Partial — вызов как tool call, результат возвращается caller'у[^9] |
| Контекст | Весь контекст передаётся[^10] | Сгенерированный input[^11] |
| Continuity | Ответ идёт от нового агента[^11] | Ответ от исходного агента[^9] |
| Use case | Эскалация, пошаговые pipeline[^10] | Субзадачи, параллельное выполнение[^9] |

**Критический инсайт:** Текущая Yodoca использует только `agent-as-tool` через `AgentProvider.invoke()`. Для сложных задач нужен **гибрид**: handoff для полной передачи (customer support escalation) и agent-as-tool для подзадач.

### 2.5 Letta/MemGPT: Stateful Memory Architecture

Letta (ex-MemGPT) — первый **stateful agent** с persistent memory. Ключевые концепции:[^12][^13]

- **Иерархическая память:** Core Memory (in-context, как RAM) → Archival Memory (persistent, как disk) → Recall Memory (search by similarity)[^14][^15]
- **Self-editing memory:** агент самостоятельно управляет перемещением данных между уровнями через tool calls[^13]
- **Memory blocks:** дискретные функциональные единицы контекста (persona, human, system) для структурированного управления context window[^16]
- **Letta V1:** отказ от `send_message` tool и `heartbeats` в пользу native reasoning и прямых assistant messages для frontier моделей (GPT-5, Claude 4.5)[^13]

### 2.6 Zep: Temporal Knowledge Graph для памяти

Zep использует **temporal knowledge graph** (Graphiti) для динамического синтеза неструктурированных разговорных данных и структурированных бизнес-данных. Результаты: 94.8% vs 93.4% MemGPT на DMR benchmark, улучшение до 18.5% accuracy и снижение latency на 90% на LongMemEval.[^17][^18]

### 2.7 A-MEM: Agentic Memory по Zettelkasten

A-MEM предлагает **agentic memory system**, где агент динамически организует память по принципам Zettelkasten — создаёт взаимосвязанные knowledge networks через dynamic indexing и linking. Это принципиально отличается от flat vector store.[^19]

***

## Часть 3: Design Patterns для SOTA Agent Systems

### 3.1 Шесть архитектурных паттернов (2025)

По результатам систематизации:[^20]

| Паттерн | Описание | Когда использовать |
|---------|----------|-------------------|
| **Evaluator-Optimizer** | Генерация + оценка + итерация | Качество критично, есть чёткие критерии |
| **Prompt Chaining** | Последовательная цепочка специализированных шагов | Задача декомпозируется на фиксированные этапы |
| **Parallelization** | Параллельные агенты на независимых подзадачах | Breadth-first исследование |
| **Routing** | Маршрутизация к специалисту по типу запроса | Ограниченное количество категорий задач |
| **Orchestrator-Workers** | Динамическая декомпозиция + делегация | Сложные, многошаговые задачи |
| **Context-Augmentation** | RAG + memory + tool results | Нужна внешняя информация |

### 3.2 Event-Driven Agent Architecture

Event-driven архитектура для AI-агентов становится стандартом:[^21][^22]
- Агенты как **event producers/consumers** вместо прямых API вызовов
- **Dynamic agent orchestration** через подписки на события
- **Fault-tolerant workflows** — event persistence гарантирует что данные не теряются при сбое агента
- **Publish-Subscribe, Event Sourcing, CQRS** как базовые паттерны

Yodoca уже имеет EventBus с durable journal — это сильный фундамент.

***

## Часть 4: Рекомендации по трансформации архитектуры Yodoca

### 4.1 Новая архитектура: Dynamic Orchestration Layer

**Предложение:** Заменить единственный Orchestrator на **Orchestration Layer** с динамическим созданием sub-агентов по модели AOrchestra + Anthropic.

```
┌─────────────────────────────────────────────────┐
│                 Orchestration Layer               │
│  ┌─────────────┐  ┌───────────┐  ┌────────────┐ │
│  │ Lead Agent   │  │ Planner   │  │ Context    │ │
│  │ (Orchestrator│  │ (Decomp.) │  │ Curator    │ │
│  └──────┬──────┘  └─────┬─────┘  └─────┬──────┘ │
│         │               │               │        │
│         ▼               ▼               ▼        │
│  ┌─────────────────────────────────────────────┐ │
│  │        Agent Factory ⟨I, C, T, M⟩           │ │
│  │  Creates specialized sub-agents on demand   │ │
│  └──────────────────┬──────────────────────────┘ │
└─────────────────────┼────────────────────────────┘
                      │
          ┌───────────┼───────────┐
          ▼           ▼           ▼
    ┌──────────┐ ┌──────────┐ ┌──────────┐
    │Sub-Agent │ │Sub-Agent │ │Sub-Agent │
    │⟨I₁,C₁,  │ │⟨I₂,C₂,  │ │⟨I₃,C₃,  │
    │ T₁,M₁⟩  │ │ T₂,M₂⟩  │ │ T₃,M₃⟩  │
    └──────────┘ └──────────┘ └──────────┘
```

### 4.2 Конкретные изменения в ядре

#### Изменение 1: AgentFactory в ядре

Новый компонент `core/agents/factory.py` — фабрика создания агентов по четвёрке `⟨I, C, T, M⟩`:

```python
@dataclass(frozen=True)
class AgentSpec:
    """Four-tuple agent specification (AOrchestra-inspired)."""
    instruction: str           # I: task objective + success criteria
    context: str | None        # C: curated working context
    tools: list[str]           # T: tool IDs from extension registry
    model: str | None          # M: model identifier for ModelRouter
    max_turns: int = 25
    
class AgentFactory(Protocol):
    """Creates specialized agents on demand."""
    async def create_agent(self, spec: AgentSpec) -> Agent: ...
    def get_available_tools(self) -> dict[str, Any]: ...
    def get_available_models(self) -> list[str]: ...
```

**Зачем:** Текущий `DeclarativeAgentAdapter` создаёт агентов при загрузке. Новая фабрика позволяет Orchestrator создавать **специализированных sub-агентов runtime**, подбирая tools и model под конкретную подзадачу.

#### Изменение 2: Context Curator

Новый protocol `ContextCurator` для **курирования контекста** при делегации:

```python
@runtime_checkable
class ContextCurator(Protocol):
    """Curates context for sub-agent creation."""
    async def curate_context(
        self,
        task: str,
        full_history: list[dict],
        relevant_tools: list[str],
    ) -> str:
        """Extract only task-relevant context from history."""
        ...
```

**Зачем:** AOrchestra показал, что curated context (96%) превосходит как no-context (86%), так и full-context (84%). Это критическое отсутствующее звено в текущей архитектуре.[^1]

#### Изменение 3: Пересмотр Orchestrator как Lead Agent

Вместо текущего «всемогущего» Orchestrator:

```python
class OrchestratorAction(Enum):
    DELEGATE = "delegate"    # Создать sub-agent и делегировать
    RESPOND = "respond"      # Ответить пользователю напрямую
    PLAN = "plan"           # Спланировать декомпозицию
    FINISH = "finish"       # Завершить задачу

# Orchestrator tools:
- delegate_task(spec: AgentSpec) -> AgentResponse
- plan_subtasks(goal: str) -> list[SubtaskPlan]  
- finish(answer: str) -> None
```

**Зачем:** По модели AOrchestra, Orchestrator должен **только оркестрировать**: планировать, делегировать, синтезировать результаты. Не выполнять задачи напрямую через extension tools.[^1]

#### Изменение 4: Hierarchical Memory System

Замена текущего ContextProvider-based memory на **трёхуровневую иерархию**:

| Уровень | Аналог | Хранение | Управление |
|---------|--------|----------|------------|
| **Working Memory** | RAM | In-context window | Automatic per-turn |
| **Episodic Memory** | SSD | SQLite + embeddings | Self-editing через tools |
| **Semantic Memory** | HDD | Knowledge graph (SQLite) | Periodic consolidation |

**Вдохновение:** Letta/MemGPT self-editing memory + Zep temporal knowledge graph + A-MEM Zettelkasten linking.[^18][^19][^13]

Ключевое: агент должен **самостоятельно управлять памятью** через tool calls (`memory_save`, `memory_search`, `memory_update`, `memory_forget`), а не получать пассивный контекст от ContextProvider.

#### Изменение 5: Tool Registry с capabilities

Вместо плоского списка tools — **реестр с метаданными**:

```python
@dataclass
class ToolDescriptor:
    id: str
    name: str
    description: str
    category: str           # "search", "code", "memory", "communication"
    capability_tags: list[str]  # ["web_search", "file_read", "code_exec"]
    cost_estimate: str      # "low", "medium", "high"
    extension_id: str
```

**Зачем:** Orchestrator должен **выбирать подмножество tools** для каждого sub-агента (T в четвёрке), а не передавать все. Текущий `loader.get_all_tools()` возвращает всё скопом.[^1]

### 4.3 Изменения в Extension Contract

#### Эволюция AgentProvider

```python
@runtime_checkable
class AgentProvider(Protocol):
    """Extension that provides a specialized AI agent — enhanced."""
    
    def get_agent_descriptor(self) -> AgentDescriptor: ...
    
    async def invoke(
        self, task: str, context: AgentInvocationContext | None = None
    ) -> AgentResponse: ...
    
    # NEW: Support for dynamic creation
    def get_tool_requirements(self) -> list[str]:
        """Tool categories this agent needs when created dynamically."""
        return []
    
    def supports_streaming(self) -> bool:
        """Whether this agent can stream intermediate results."""
        return False
```

#### Новый protocol: MemoryProvider

```python
@runtime_checkable
class MemoryProvider(Protocol):
    """Hierarchical memory management."""
    
    async def save(self, key: str, content: str, 
                   level: Literal["working", "episodic", "semantic"]) -> None: ...
    async def search(self, query: str, limit: int = 5) -> list[MemoryEntry]: ...
    async def consolidate(self) -> None:
        """Periodic: move working → episodic, extract semantic."""
        ...
```

***

## Часть 5: Конкурентный анализ

### Сравнение с рыночными решениями

| Решение | Архитектура | Memory | Dynamic Agents | Local-first | Open Source |
|---------|-------------|--------|----------------|-------------|-------------|
| **Yodoca (текущая)** | Single Orchestrator + Extensions | ContextProvider (flat) | Declarative only | ✅ | ✅ |
| **Letta/MemGPT** | Stateful agent loop | Self-editing hierarchical | ❌ (fixed agents) | ✅ | ✅ |
| **CrewAI** | Role-based crew | Basic delegation | Static roles | ❌ (cloud) | ✅ |
| **LangGraph** | Graph-based state machine | Checkpointing | Via recompilation | ❌ | ✅ |
| **OpenAI Agents SDK** | Handoff/Tool pattern | Sessions (SQLite) | Manual only | ❌ | ✅ |
| **Claude Code** | Orchestrator + fixed sub-agents | Context windows | Static specialists | ❌ | ❌ |
| **Shinkai** | Local agent creator | Basic | UI-based creation | ✅ | ✅ |
| **AOrchestra** | Dynamic 4-tuple factory | Curated context | ✅ Full dynamic | ❌ (research) | ✅ |
| **Yodoca (target)** | Dynamic Orchestration Layer | Hierarchical self-editing | ✅ Runtime factory | ✅ | ✅ |

### Уникальное позиционирование Yodoca

Ни одно из решений не объединяет: **local-first** + **dynamic sub-agent creation** + **hierarchical memory** + **event-driven architecture** + **extension ecosystem**. Это потенциальная ниша Yodoca.

***

## Часть 6: Дорожная карта трансформации

### Фаза 1: Foundation (Breaking Changes)

1. **Tool Registry** — добавить `ToolDescriptor` с метаданными в `core/extensions/loader.py`
2. **AgentFactory** — создать `core/agents/factory.py` с четвёрка-интерфейсом
3. **Context Curator** — новый protocol в `core/extensions/contract.py`
4. **Orchestrator Refactor** — из "делает всё" в "только delegate/plan/finish"

### Фаза 2: Memory Revolution

5. **MemoryProvider protocol** — трёхуровневая иерархия в `core/extensions/contract.py`
6. **Self-editing memory extension** — переписать `sandbox/extensions/memory/` с memory tools
7. **Temporal indexing** — SQLite-based knowledge graph для semantic memory

### Фаза 3: Advanced Orchestration

8. **Complexity estimator** — анализ сложности запроса для выбора стратегии (single vs multi-agent)
9. **Parallel sub-agents** — поддержка параллельного выполнения через EventBus
10. **Orchestration learning** — in-context learning для оптимизации cost/performance

### Фаза 4: Competitive Edge

11. **Streaming sub-agents** — intermediate results через StreamingChannelProvider
12. **Agent self-improvement** — агенты переписывают tool descriptions (по опыту Anthropic)[^4]
13. **Skill learning** — агент учится на опыте, сохраняя паттерны в semantic memory (по модели Letta)[^16]

***

## Ключевые научные источники

| Источник | Вклад | Применимость к Yodoca |
|----------|-------|-----------------------|
| **AOrchestra (ICLR 2025)** | 4-tuple agent abstraction, dynamic creation | Прямая — заменяет статичных AgentProvider |
| **Anthropic Multi-Agent (2025)** | Orchestrator-worker, delegation guidelines | Высокая — паттерн для нового Orchestrator |
| **Letta V1 (2025)** | Native reasoning, self-editing memory | Высокая — модель для Memory extension |
| **Zep (2025)** | Temporal knowledge graph memory | Средняя — для semantic memory layer |
| **A-MEM (2025)** | Zettelkasten-style linked memory | Средняя — для knowledge organization |
| **Flow (ICLR 2025)** | Modular workflow automation | Средняя — для workflow patterns |
| **ADAS (2025)** | Automated design of agentic systems | Вдохновение — self-evolving architecture |

---

## References

1. [AOrchestra: Automating Sub-Agent Creation for Agentic Orchestration](https://arxiv.org/html/2602.03786v2)

2. [AOrchestra: Automating Sub-Agent Creation for Agentic Orchestration](https://arxiv.org/html/2602.03786v1) - Other practical systems, such as Claude Code (Anthropic, 2025) , support sub-agents that operate wit...

3. [AOrchestra: Automating Sub-Agent Creation for Agentic Orchestration](https://github.com/FoundationAgents/AOrchestra) - Our core claim is that agent orchestration becomes modular, controllable, and plug-and-play when we ...

4. [How we built our multi-agent research system - Anthropic](https://www.anthropic.com/engineering/multi-agent-research-system) - Our Research feature uses multiple Claude agents to explore complex topics more effectively. We shar...

5. [Anthropic: Building Production Multi-Agent Research Systems with ...](https://www.zenml.io/llmops-database/building-production-multi-agent-research-systems-with-claude) - Anthropic developed a production-grade multi-agent research system for their Claude Research feature...

6. [GitHub - SuperAce100/symphony: Hierarchical Sub-Agent Creation and Orchestration for Multi-Agent Applications](https://github.com/SuperAce100/symphony) - Hierarchical Sub-Agent Creation and Orchestration for Multi-Agent Applications - SuperAce100/symphon...

7. [openai-agents-python/README.md at main · openai/openai-agents-python](https://github.com/openai/openai-agents-python/blob/main/README.md) - A lightweight, powerful framework for multi-agent workflows - openai/openai-agents-python

8. [openai-agents-python/docs/handoffs.md at main · openai/openai-agents-python](https://github.com/openai/openai-agents-python/blob/main/docs/handoffs.md) - A lightweight, powerful framework for multi-agent workflows - openai/openai-agents-python

9. [Multi-Agent Portfolio Collaboration with OpenAI Agents SDK](https://developers.openai.com/cookbook/examples/agents_sdk/multi-agent-portfolio-collaboration/multi_agent_portfolio_collaboration/) - In a handoff architecture, each agent knows about the others and can decide when to defer to a more ...

10. [practical experiences and opinions on the “handoff” vs. “agent-as-tool” approaches in agent systems, including real-world project examples and specific frameworks like LangChain, CrewAI, AutoGPT, and others.](https://gist.github.com/mkbctrl/555b84c8dd4a74720d2983ab4e75bbaa) - practical experiences and opinions on the “handoff” vs. “agent-as-tool” approaches in agent systems,...

11. [Agentic Delegation: LangGraph vs OpenAI vs Google ADK](https://www.arcade.dev/blog/agent-handoffs-langgraph-openai-google/) - Handoffs: This is a tool call where the control of the flow is fully delegated to the target agent, ...

12. [GitHub - letta-ai/letta: Letta is the platform for building stateful agents](https://github.com/letta-ai/letta) - Letta is the platform for building stateful agents: AI with advanced memory that can learn and self-...

13. [Lessons from ReAct, MemGPT, & Claude Code](https://www.letta.com/blog/letta-v1-agent) - MemGPT was another early agent architecture and notably the first example of a stateful agent with p...

14. [MemGPT: Towards LLMs as Operating Systems](https://arxiv.org/pdf/2310.08560.pdf) - ...introduce MemGPT
(Memory-GPT), a system that intelligently manages different memory tiers in
orde...

15. [Agent Memory: How to Build Agents that Learn and Remember - Letta](https://www.letta.com/blog/agent-memory) - Traditional LLMs operate in a stateless paradigm—each interaction exists in isolation, with no knowl...

16. [MemGPT is now part of Letta](https://www.letta.com/blog/memgpt-and-letta) - Introducing Letta's new agent architecture, optimized for frontier reasoning models. Sep 30, 2025. I...

17. [Zep Is The New State of the Art In Agent Memory](https://blog.getzep.com/state-of-the-art-agent-memory/) - Setting a new standard for agent memory with up to 100% accuracy gains and 90% lower latency.

18. [Zep: A Temporal Knowledge Graph Architecture for Agent Memory](https://arxiv.org/abs/2501.13956) - We introduce Zep, a novel memory layer service for AI agents that outperforms the current state-of-t...

19. [A-MEM: Agentic Memory for LLM Agents](https://arxiv.org/pdf/2502.12110.pdf) - ...storage and
retrieval but lack sophisticated memory organization, despite recent attempts
to inco...

20. [GitHub - dcarpintero/agentic-ai: A comprehensive guide to building and orchestrating AI Agents using six foundational design patterns: Evaluator-Optimizer, Context-Augmentation, Prompt-Chaining, Parallelization, Routing, and Orchestrator-Workers.](https://github.com/dcarpintero/agentic-ai) - A comprehensive guide to building and orchestrating AI Agents using six foundational design patterns...

21. [Deep Dive into Advanced Agent Event-Driven Architecture](https://sparkco.ai/blog/deep-dive-into-advanced-agent-event-driven-architecture)

22. [Real-World Ai Agent Impact...](https://www.linkedin.com/pulse/event-driven-ai-agents-architecture-pattern-every-needs-venkatesan-fzwfc) - The $100 Million Question Every Tech Leader and CTO is Asking Your AI pilots are working. Customer s...

