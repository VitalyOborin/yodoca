# ADR 015: Agent Skills System

## Status

Proposed

## Context

### What exists today

AI agents in the system (Orchestrator, `builder_agent`, `task_engine` workers) operate with procedural knowledge baked directly into their prompts. For example, `builder_agent/prompt.jinja2` contains 158 lines of contract documentation, protocol definitions, ExtensionContext API reference, and example patterns — all hardcoded. This knowledge is unavailable to any other agent.

This creates several problems:

| Problem | Detail |
|---------|--------|
| **Knowledge is locked inside prompts** | `builder_agent` carries the extension contract in its prompt. If `task_engine`'s worker or the Orchestrator needs the same knowledge (e.g. to reason about an extension), it cannot access it. |
| **Prompts conflate role and knowledge** | A single `prompt.jinja2` mixes the agent's behavioral instructions ("you are the builder; here's your workflow") with reference material ("here's the ContextProvider protocol signature"). The agent's prompt grows linearly with the amount of knowledge it needs. |
| **No reuse across agents** | Creating a second agent that needs similar knowledge (e.g. an "extension reviewer" agent) requires copy-pasting the same contract documentation. Changes to the contract must be replicated manually. |
| **Context window bloat** | Agents carry all their knowledge in every invocation, even when the current task needs only a fraction of it. A "create a ToolProvider" request forces the agent to also carry ChannelProvider, SchedulerProvider, and EventBus documentation it won't use. |

### Industry landscape

The industry has converged on a standard approach for equipping AI agents with reusable procedural knowledge: **Agent Skills**.

| System | Format | Discovery | Loading | Key feature |
|--------|--------|-----------|---------|-------------|
| **Claude Code** | SKILL.md + YAML frontmatter | Filesystem scan (`.claude/skills/`) | AI reads via bash on demand | Three-level progressive disclosure |
| **Cursor** | SKILL.md (same format) | `.cursor/skills/`, `.agents/skills/` | Dynamic context discovery by agent | Cross-platform compatibility with Claude Code |
| **OpenClaw** | SKILL.md + extended metadata | Workspace > managed > bundled (three-tier precedence) | Tool dispatch or model invocation | ClawHub registry for sharing, live reloading |
| **Academic (SoK: Agentic Skills, 2025)** | Multiple representations | Metadata-driven discovery | Progressive disclosure | "Curated skills substantially improve agent success rates" |

The de-facto standard is: **SKILL.md with YAML frontmatter, directory-per-skill, three-level progressive disclosure** (metadata → instructions → resources).

The critical difference for Yodoca: in IDEs the AI reads skills from the filesystem via bash. In our platform, agents interact with knowledge through ExtensionContext — skills need a runtime integration layer that fits the all-is-extension principle.

### Design goals

- **Reusable knowledge** — procedural knowledge written once, available to any agent.
- **Progressive disclosure** — agents load only what they need, when they need it.
- **Standard format** — SKILL.md compatible with Cursor, Claude Code, and OpenClaw.
- **Composable** — implemented as an extension, not a core component.
- **No core changes in Phase 1** — skills work through existing ToolProvider + ContextProvider protocols.
- **Self-improving** — agents can create and propose updates to skills.

## Decision

### 1. Skill format (standard SKILL.md)

Each skill is a directory under `sandbox/skills/<skill-id>/` containing a `SKILL.md` file with YAML frontmatter:

```
sandbox/skills/
├── extension-development/
│   ├── SKILL.md
│   ├── references/
│   │   └── protocols.md
│   └── templates/
│       ├── manifest-tool-provider.yaml
│       └── main-tool-provider.py
├── code-review/
│   └── SKILL.md
└── task-decomposition/
    └── SKILL.md
```

`SKILL.md` follows the standard format:

```markdown
---
name: extension-development
description: >
  How to create and modify extensions for the Yodoca platform.
  Use when building new tools, channels, agents, schedulers, or services.
tags: [development, extensions, architecture]
requires: []
---

# Extension Development

## When to use
- Creating a new extension (tool, channel, agent, scheduler, service)
- Understanding the extension contract and protocols

## Extension Contract
...detailed instructions...
```

**Required fields:** `name`, `description`.

**Optional fields:**

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `tags` | `list[str]` | `[]` | Keywords for filtering and search |
| `requires` | `list[str]` | `[]` | Skill IDs that should be loaded before this skill. When an agent calls `read_skill("api-design")` and the skill declares `requires: [extension-development]`, the tool response includes a hint: "This skill requires: extension-development. Load it first if you haven't already." The agent decides whether to follow the hint — there is no automatic transitive loading (to keep token usage explicit). |

Sub-directories (`references/`, `templates/`, `scripts/`) contain supplementary materials the agent can load on demand. Naming is by convention; the skill_registry does not impose a fixed sub-directory structure.

### 2. Three-level progressive disclosure

The core mechanism for context-efficient knowledge delivery:

| Level | When loaded | Token cost | Content | Mechanism |
|-------|-----------|------------|---------|-----------|
| **L1: Catalog** | Every agent invocation | ~20 tokens/skill | Name + one-line description | `ContextProvider.get_context()` |
| **L2: Instructions** | Agent calls `read_skill()` | ~2–5K tokens | Full SKILL.md body | `ToolProvider` tool |
| **L3: Resources** | Agent calls `read_skill_resource()` | Variable | Templates, references, checklists | `ToolProvider` tool |

**Why three levels?** L1 is cheap enough to always inject (~200 tokens for 10 skills). L2 and L3 are agent-driven: the LLM recognizes a need from the L1 catalog and explicitly requests detail. This avoids attention dilution — the Orchestrator is never forced to process extension contract documentation when the user asks about the weather.

### 3. Skill Registry extension

A new extension at `sandbox/extensions/skill_registry/`:

```
sandbox/extensions/skill_registry/
├── manifest.yaml
├── main.py               # SkillRegistry: discovery, index, tools, context
└── registry.py            # SkillIndex: parse, store, query
```

The extension implements three protocols:

| Protocol | Role |
|----------|------|
| **ToolProvider** | Exposes `list_skills()`, `read_skill(skill_id)`, `read_skill_resource(skill_id, path)` |
| **ContextProvider** | Injects L1 catalog into every agent invocation (priority 60, between channel context at 0 and memory at 100) |
| **ServiceProvider** | Runs a lightweight directory watcher for hot-reloading |

#### Discovery and indexing

On `initialize()`:
1. Scan `sandbox/skills/` for directories containing `SKILL.md`.
2. Parse YAML frontmatter (`name`, `description`, `tags`).
3. Build an in-memory index: `dict[str, SkillMetadata]`.

The index is lightweight (frontmatter only, no full content in memory).

#### ContextProvider (L1 injection)

```python
@property
def context_priority(self) -> int:
    return 60

async def get_context(self, prompt: str, turn_context: TurnContext) -> str | None:
    if not self._index:
        return None
    lines = ["[Available Skills]"]
    for skill_id, skill in self._index.items():
        lines.append(f"• `{skill_id}` — {skill.description_short}")
    lines.append("")
    lines.append("Use read_skill(skill_id) to load full instructions.")
    return "\n".join(lines)
```

The catalog uses `skill_id` (the directory name, e.g. `extension-development`) as the primary identifier in the output. This ensures the agent passes the exact ID to `read_skill()` rather than a human-readable name that would fail lookup.

Every agent invocation (Orchestrator, declarative agents via `enrich_prompt`) sees the catalog. The agent decides whether to load L2/L3 based on task relevance.

#### ToolProvider tools

Three tools:

**`list_skills(query: str = "")`** — Returns skill catalog with descriptions. Optional `query` filters by name/tag substring match. For agents that want structured discovery.

**`read_skill(skill_id: str)`** — Returns the full SKILL.md content (L2). The agent calls this when it recognizes a skill is relevant to the current task. The response includes token-count metadata (e.g. "Skill loaded: extension-development (approx. 3200 tokens)") so the agent can reason about context budget. If the skill declares `requires`, the response appends a hint listing prerequisite skill IDs.

**`read_skill_resource(skill_id: str, path: str)`** — Returns a specific file from the skill directory (L3). For templates, reference docs, checklists. Path is validated to prevent traversal outside the skill directory (`sandbox/skills/<skill_id>/`).

Path traversal protection:

```python
base = self._skills_dir / skill_id
resolved = (base / path).resolve()
if not resolved.is_relative_to(base.resolve()):
    return ToolError("Path traversal detected")
```

#### ServiceProvider (hot-reloading)

The `skill_registry` implements `ServiceProvider` with `run_background()` that periodically rescans the skills directory (every 5 seconds). When a SKILL.md file is added, modified, or removed, the in-memory index is updated. The next agent invocation sees the changes without a system restart.

This is essential because:
- The `builder_agent` can create new skills at runtime.
- Human operators can edit SKILL.md files while the system is running.
- The overhead is negligible (directory stat checks, no file content loaded until requested).

### 4. Static skill binding: `uses_skills` (Phase 2)

For specialist agents where certain skills are always relevant, a new manifest field:

```yaml
# sandbox/extensions/builder_agent/manifest.yaml
id: builder_agent
name: Extension Builder Agent

agent:
  integration_mode: tool
  model: gpt-5.2-codex
  uses_tools:
    - core_tools
    - web_search
    - skill_registry
  uses_skills:
    - extension-development
  limits:
    max_turns: 20

depends_on:
  - skill_registry
```

**Manifest change:** Add `uses_skills: list[str]` to `AgentManifestConfig` in `core/extensions/manifest.py`. Default: `[]`.

**Loader change:** In `_resolve_agent_instructions()`, after resolving `prompt.jinja2` and inline `instructions`, the Loader calls `skill_registry.get_skill_content(skill_id)` for each entry in `uses_skills` and appends the full SKILL.md body to `resolved_instructions`.

Resolution order for agent instructions:

```
1. prompt.jinja2 (from extension dir)          — role and workflow
2. SKILL.md content (from uses_skills)          — procedural knowledge
3. agent.instructions (from manifest, inline)   — overrides / additions
```

This means `builder_agent/prompt.jinja2` shrinks from 158 lines to ~30 lines (role + workflow only). The contract documentation moves to `sandbox/skills/extension-development/SKILL.md` and becomes reusable.

**Why `uses_skills` needs a core change:** The field is semantically a sibling of `uses_tools`. Resolving it at load time (not at runtime via tool calls) guarantees that specialist agents always have the knowledge "in blood" — no risk of the agent forgetting to call `read_skill()`. This eliminates a failure mode where an agent starts writing code without loading the relevant skill.

The core change is minimal: one field in a Pydantic model (`manifest.py`) and ~10 lines in `Loader._resolve_agent_instructions()`.

### 5. Agent flow examples

#### Orchestrator handling "create an RSS extension"

```
User: "Create an extension that monitors RSS feeds"

1. [ContextProvider injects L1]
   [Available Skills]
   • `extension-development` — Create/modify extensions, protocols, manifests
   • `code-review` — Code review checklist and best practices
   • `task-decomposition` — Break complex tasks into subtasks
   Use read_skill(skill_id) to load full instructions.

2. [Orchestrator recognizes: this needs builder_agent]
   → Invokes builder_agent tool

3. [builder_agent already has extension-development skill via uses_skills]
   → No tool call needed, contract knowledge is in system prompt
   → Reads a template: read_skill_resource("extension-development", "templates/manifest-tool-provider.yaml")
   → Generates manifest.yaml + main.py following the template
   → Calls request_restart()
```

#### Task Engine worker using a skill on demand

```
Worker executing task: "Review the kv extension for best practices"

1. [Worker receives L1 catalog via enrich_prompt]
   Sees "code-review" skill available

2. [Worker loads L2]
   → read_skill("code-review")
   → Gets review checklist, patterns to look for, common mistakes

3. [Worker uses file tools to read kv extension code]
   → Applies checklist from the skill
   → Produces structured review result
```

### 6. Refactoring builder_agent

The `builder_agent/prompt.jinja2` is split as follows:

| Current location | New location | Content |
|-----------------|-------------|---------|
| `prompt.jinja2` lines 1–10 | `prompt.jinja2` (stays) | Role definition, working directory, constraints |
| `prompt.jinja2` lines 11–100+ | `sandbox/skills/extension-development/SKILL.md` | Contract (protocols), ExtensionContext API, Event Bus, manifest schema |
| `prompt.jinja2` examples | `sandbox/skills/extension-development/references/` | Reference extensions, patterns, examples |

After refactoring, `builder_agent/manifest.yaml` adds `uses_skills: [extension-development]` and `depends_on: [skill_registry]`. The prompt.jinja2 retains only:

```jinja2
You are the Extension Builder Agent.
You can create/update/delete extensions for the nano-kernel.

## Working directory
Your working directory is the sandbox root. ...

## Workflow
1. Understand the user's request
2. Choose the right extension type (tool, channel, agent, etc.)
3. Generate manifest.yaml and code following the extension-development skill
4. Use templates from read_skill_resource() when available
5. Call request_restart() to activate

## Constraints
- Use context.get_secret() for secrets (never hardcode)
- You cannot call lifecycle methods — only write files and restart
```

### 7. Skill self-improvement (Phase 4)

Agents can create new skills by writing files to `sandbox/skills/<new-id>/SKILL.md` via core file tools. The hot-reloading ServiceProvider picks up the new skill automatically.

For updates to existing skills, a **Skill Proposal** mechanism prevents uncontrolled modification of system knowledge:

- Tool: `propose_skill_update(skill_id, diff, justification)` — writes a structured proposal to `sandbox/data/skill_registry/proposals/<skill_id>-<timestamp>.md`.
- Each proposal contains three sections:
  - **Diff** — the specific changes to SKILL.md (additions, deletions, modifications).
  - **Justification** — why the agent believes the change is necessary (e.g. "Protocol signature changed: `get_context` now accepts `TurnContext` instead of `agent_id`; current skill content causes the builder to generate incompatible code").
  - **Evidence** — optional references to errors, test failures, or user feedback that triggered the proposal.
- Proposals are reviewed by the user (via CLI/Telegram notification) or by a designated reviewer agent.
- Only after approval does the actual SKILL.md get updated.

This prevents a hallucination loop where an agent degrades its own procedural knowledge. Structured proposals with diffs and justifications make human review fast and informed. Curated skills are consistently more effective than self-generated ones (SoK: Agentic Skills, 2025).

### 8. Implementation phases

#### Phase 1: Foundation (no core changes)

- Create `sandbox/skills/` directory.
- Create `skill_registry` extension: ToolProvider + ContextProvider + ServiceProvider.
- Extract `builder_agent` contract knowledge into `sandbox/skills/extension-development/SKILL.md`.
- `builder_agent` uses `read_skill()` tool calls to access knowledge (not yet static binding).
- Add `skill_registry` to `builder_agent`'s `uses_tools` and `depends_on`.

**Deliverables:** Working skill catalog, three tools, L1 context injection, hot-reloading.

#### Phase 2: Static binding

- Add `uses_skills: list[str]` to `AgentManifestConfig`.
- Update `Loader._resolve_agent_instructions()` to resolve skills.
- Update `builder_agent` manifest: `uses_skills: [extension-development]`.
- Trim `builder_agent/prompt.jinja2` to role + workflow only.

**Deliverables:** Specialist agents always carry relevant skills. Shorter, cleaner prompts.

#### Phase 3: Smart filtering and search

- Embedding-based skill relevance scoring in ContextProvider (via `embedding` extension).
- SQLite + FTS5 catalog cache for fast search across large skill sets.
- Agent-scoped catalog: ContextProvider uses `turn_context.agent_id` to filter — specialist agents see only their declared skills, Orchestrator sees the full catalog.
- `list_skills(query)` uses FTS5/embeddings instead of substring match.

**Deliverables:** Scalable to hundreds of skills without L1 catalog bloat.

#### Phase 4: Self-improvement

- `propose_skill_update()` tool + proposal review workflow.
- `builder_agent` can create new skills (not just extensions).
- Skill versioning and change tracking in `sandbox/data/skill_registry/`.

**Deliverables:** Evolving knowledge base with human-in-the-loop governance.

## Consequences

### Positive

- **Reusable knowledge** — contract documentation, review checklists, and design patterns are written once and available to any agent. Adding a new agent that needs extension knowledge takes one line (`uses_skills: [extension-development]`), not 100 lines of copy-pasted prompt.
- **Smaller, focused prompts** — agent prompts describe behavior (role, workflow, constraints). Reference material lives in skills. This makes prompts easier to maintain and debug.
- **Context efficiency** — the Orchestrator carries ~200 tokens of skill catalog instead of several thousand tokens of contract documentation. Full knowledge is loaded only when needed.
- **Standard compatibility** — SKILL.md format is compatible with Cursor, Claude Code, and OpenClaw. Skills authored for Yodoca can be reused for development-time assistance, and vice versa.
- **Self-improvement path** — the architecture supports agents creating and refining skills, with governance guardrails to prevent knowledge degradation.
- **No core changes in Phase 1** — the entire skill system works through existing protocols. Phase 2 adds one Pydantic field and ~10 lines in Loader.

### Trade-offs

- **New extension** — `skill_registry` adds a component to manage. However, it is simple (no database, no complex state) and follows the all-is-extension principle.
- **L1 token overhead** — the skill catalog is injected into every agent invocation. At ~20 tokens per skill, 10 skills cost ~200 tokens — negligible. At 100+ skills (Phase 3), smart filtering becomes necessary.
- **Agent must decide to load skills** — for the Orchestrator (no `uses_skills`), the agent must recognize when a skill is relevant and call `read_skill()`. LLMs are generally good at this pattern ("recognized need → requested tool"), but there is a failure mode where the agent skips loading a relevant skill. Mitigated by `uses_skills` for specialist agents and by clear skill descriptions.
- **Core change in Phase 2** — adding `uses_skills` to manifest parser touches `core/extensions/manifest.py` and `core/extensions/loader.py`. The change is minimal (~15 lines total) and follows the existing `uses_tools` pattern. Justified by the elimination of runtime overhead and failure modes for specialist agents.
- **Hot-reloading overhead** — directory polling every 5 seconds. The cost is negligible (stat calls only, no file reads) but adds a background task. Can be disabled via config if not needed.

### What stays the same

- **Extension system** — all existing protocols and wiring unchanged.
- **Orchestrator** — gains new tools from `skill_registry` but its architecture is unchanged.
- **ContextProvider pipeline** — `skill_registry` is one more provider in the existing chain.
- **Agent prompts** — existing agents without `uses_skills` continue to work exactly as before.
- **Event Bus, MessageRouter, ModelRouter** — no changes.

### Risks

| Risk | Severity | Mitigation |
|------|----------|------------|
| **Agent ignores relevant skill** — Orchestrator does not call `read_skill()` when it should | Medium | Clear L1 descriptions. `uses_skills` for specialist agents eliminates this risk for them. Prompt engineering: "Always check available skills before starting a complex task." |
| **Skill content drift** — SKILL.md becomes outdated as the extension contract evolves | Medium | Include skill review in contract change workflow. Phase 4 proposal mechanism enables agent-driven updates with human review. |
| **Path traversal in `read_skill_resource()`** | High | Strict path validation: resolve path, assert `is_relative_to(skill_base_dir)`. Reject `..` segments. |
| **L1 catalog grows too large** | Low | Phase 3 introduces filtering. Until then, practical limit is ~50 skills (~1000 tokens), well within budget. |
| **Skill SKILL.md too large** — a single skill consumes excessive context when loaded via `read_skill()` | Medium | `read_skill` returns approximate token count in metadata so the agent can reason about budget. Recommended soft limit: 8000 tokens per SKILL.md. Larger skills should be split into sub-skills or move detail into `references/` (L3). The registry logs a warning during indexing if a SKILL.md exceeds the limit. |
| **Hallucination loop in self-improvement** | Medium | Phase 4 uses proposal mechanism (not direct writes). Human-in-the-loop review before applying changes to SKILL.md. |

## Alternatives Considered

**Inject all knowledge into agent prompts (status quo).** Keep procedural knowledge in `prompt.jinja2` per agent. Rejected because it leads to prompt duplication, context bloat, and makes knowledge unavailable to other agents.

**Core-level skill loader (not an extension).** Add skill discovery and injection to the Loader in `core/`. Rejected because it violates the all-is-extension principle. Skill management is not a kernel responsibility — it is a specialized form of context enrichment, which is exactly what ContextProvider and ToolProvider are for.

**Skills as ContextProvider only (no tools).** Inject relevant skill content automatically, without `read_skill()` tools. Rejected because automatic injection requires relevance scoring on every invocation (LLM call or embedding computation), adding latency and cost. The tool-based approach lets the agent decide, matching the proven "recognized need → requested tool" pattern. Auto-injection is reconsidered in Phase 3 with embedding-based scoring.

**Skills as standalone files read via core `file` tool.** Agents already have `file_read` — they could read `sandbox/skills/<id>/SKILL.md` directly. Rejected because: (1) the agent has no discovery mechanism — it must know skill paths in advance; (2) no L1 catalog injection — the agent doesn't know what skills exist; (3) no path traversal protection specific to skills; (4) no structured skill metadata for filtering.

**MCP-based skill server.** Expose skills via an MCP server. Rejected for Phase 1 as over-engineering — skills are local files, not remote resources. MCP integration can be added later if skills need to be shared across systems.

## References

- [Agent Skills — Anthropic Platform Docs](https://platform.claude.com/docs/en/agents-and-tools/agent-skills/overview)
- [Agent Skills — Cursor Docs](https://cursor.com/docs/context/skills)
- [Skills — OpenClaw Docs](https://docs.openclaw.ai/skills)
- [SoK: Agentic Skills — Beyond Tool Use in LLM Agents (arXiv, 2025)](https://arxiv.org/html/2602.20867v1)
- [AI Agent Plugin and Extension Architecture — Zylos Research](https://zylos.ai/research/2026-02-21-ai-agent-plugin-extension-architecture)
- ADR 002: Nano-Kernel + Extensions
- ADR 003: Agent-as-Extension
- ADR 014: Task Engine and Agent Loop
