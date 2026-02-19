# Yodoca — Автономный ИИ агент

Экспериментальное приложение — **Agentic OS**: среда выполнения для ИИ-агента, где почти вся функциональность реализована как расширения. Ядро минимально: управление жизненным циклом, шина событий и контроль безопасности. Всё остальное — каналы связи, инструменты, фоновые сервисы — подключается как расширения.

## Основные принципы
- **Microkernel** - вся основная минимально необходимая логика сконцентрирована в ядре с ИИ агентом
- **Extension-first** - любое расширение системы возможно за счет изолированных extensions, которые выполняют контракты.
- **Security by design** - безопасность принимаемых решений, HITL, требуется подтверждение от пользователя для опасных действий.

## Чем занимается проект

- **Оркестратор** — основной агент, который общается с пользователем, проверяет доступные расширения и выбирает подходящие инструменты.
- **Создание расширений** — если нужной возможности нет, агент может предложить создать новое расширение через Builder Agent (с подтверждением пользователя).
- **Расширяемая архитектура** — типы расширений: `tool`, `channel`, `service`, `scheduler`, `monitor`, `middleware`. Расширения общаются через Event Bus, не зная друг о друге напрямую.
- **Мультиканальность** — CLI и Telegram (и другие каналы через расширения). Сообщения приходят как `user_message`, ответы уходят как `agent_response`.
- **Безопасность** — права доступа, секреты, human-in-the-loop при активации новых расширений.
- **Песочница** — расширения работают в `sandbox/`, ядро в `ai/` не трогают.

## Запуск

### Требования
- Python 3.12+
- Зависимости (установить: `uv sync`)
- Файл `.env` в корне проекта с переменными окружения (например, `OPENAI_API_KEY`)

### Запуск приложения
```bash
uv run python -m supervisor
```

## Конфигурация провайдеров и моделей

Назначение LLM провайдеров и моделей задаётся в **`config/settings.yaml`**: блок **`agents`** (провайдер и модель для каждого агента) и блок **`providers`** (описание API: тип, base_url, ключи). Ядро (модуль `core/llm/`) читает настройки при старте и по `agent_id` отдаёт каждому агенту готовый экземпляр модели (OpenAI Agents SDK). Смена провайдера или модели — правка YAML, без изменений кода.

### Структура конфигурации

- **`agents`** — для каждого агента: `provider`, `model`, при необходимости `instructions`, `temperature`, `max_tokens`.
- **`providers`** — список провайдеров (OpenAI, LM Studio, Anthropic, OpenRouter и т.д.). Для неизвестного `agent_id` используется запись **`default`** в блоке `agents` (если задана).

### Провайдеры

Каждый провайдер задаётся полем `type` и опционально `base_url`, ключом API и заголовками:

| Поле | Описание |
|------|----------|
| `type` | `openai_compatible` (OpenAI, LM Studio, OpenRouter) или `anthropic` |
| `base_url` | URL API. Не указывать — используется `https://api.openai.com/v1` для OpenAI |
| `api_key_secret` | Имя переменной окружения с ключом (например `OPENAI_API_KEY`) |
| `api_key_literal` | Фиксированная строка вместо ключа (для LM Studio часто `lm-studio`) |
| `default_headers` | Доп. заголовки запросов (например для OpenRouter: `HTTP-Referer`, `X-Title`) |

**В том же файле `config/settings.yaml` задаётся блок `agents` и блок `providers`. Примеры:**

```yaml
agents:
  orchestrator:
    provider: openai
    model: gpt-5.2
    instructions: prompts/orchestrator.jinja2

providers:
  openai:
    type: openai_compatible
    api_key_secret: OPENAI_API_KEY

  lm_studio:
    type: openai_compatible
    base_url: http://127.0.0.1:1234/v1
    api_key_literal: lm-studio

  anthropic:
    type: anthropic
    api_key_secret: ANTHROPIC_API_KEY

  openrouter:
    type: openai_compatible
    base_url: https://openrouter.ai/api/v1
    api_key_secret: OPENROUTER_API_KEY
    default_headers:
      HTTP-Referer: https://your-app.example.com
      X-Title: YourApp
```

Для провайдера **Anthropic** нужна опциональная зависимость: `uv sync --extra litellm` (или `pip install 'openai-agents[litellm]'`).

### Агенты

В блоке **`agents`** каждому агенту задаётся провайдер, модель и при необходимости параметры:

| Поле | Описание |
|------|----------|
| `provider` | ID провайдера из блока `providers` |
| `model` | Имя модели у этого провайдера (например `gpt-4o`, `mistralai/codestral-22b-v0.1`, `claude-3-5-sonnet-20241022`) |
| `temperature` | Опционально, по умолчанию 0.7 |
| `max_tokens` | Опционально |

**Примеры:**

```yaml
agents:
  default:
    provider: openai
    model: gpt-5

  orchestrator:
    provider: lm_studio
    model: mistralai/codestral-22b-v0.1
    temperature: 0.7

  builder:
    provider: anthropic
    model: claude-3-5-sonnet-20241022
    temperature: 0.2
```

- **`orchestrator`** — основной агент приложения.
- **`default`** — используется для любого агента (в т.ч. из расширений), для которого нет отдельной записи.
- Имена вроде **`builder`**, **`memory_consolidator`** — для агентов расширений; расширение указывает свой `agent_id` в манифесте (или используется id расширения).

### Расширения с агентами

Чтобы расширение-агент использовало конкретную модель из `config/settings.yaml`:

1. В **`config/settings.yaml`** в блоке `agents` добавьте запись с нужным `agent_id` (например, id расширения или осмысленное имя).
2. В **`manifest.yaml`** расширения при необходимости укажите **`agent_id`** — по нему ядро вызовет `model_router.get_model(agent_id)`.

Пример манифеста:

```yaml
id: builder_agent
name: Extension Builder Agent
agent_id: builder   # соответствует agents.builder в config/settings.yaml
agent:
  integration_mode: tool
  model: gpt-5.2-codex   # игнорируется при наличии model_router; иначе fallback
  instructions_file: prompts/builder.jinja2
  ...
```

Если **`agent_id`** не указан, используется **id расширения** (например `builder_agent`). Тогда в `config/settings.yaml` в блоке `agents` должна быть запись с таким ключом или будет использован **`default`**.

Расширение может объявить свою конфигурацию модели прямо в манифесте через **`agent_config`** (без правки общего `config/settings.yaml`):

```yaml
id: memory
name: Cognitive Memory
agent_config:
  consolidator_agent:
    provider: lm_studio
    model: qwen2.5-7b
    temperature: 0.3
```

Так в роутер добавляются записи для `consolidator_agent` и т.п.; код расширения получает `ModelRouter` через `ExtensionContext.model_router` и вызывает `get_model("consolidator_agent")`.

### Секреты

Ключи API не хранятся в YAML. Используйте:

- **`api_key_secret`** — имя переменной окружения (значение берётся из `.env` / окружения).
- **`api_key_literal`** — фиксированная строка (удобно для локальных провайдеров вроде LM Studio, где ключ не нужен).

В корне проекта должен быть файл **`.env`** с нужными переменными (например `OPENAI_API_KEY`, `ANTHROPIC_API_KEY`). Вся конфигурация провайдеров и агентов — в одном файле **`config/settings.yaml`** (блоки `agents` и `providers`).