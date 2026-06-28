# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this repository is

This is a leaked source code snapshot of **Claude Code** — Anthropic's official CLI tool. It is a study/reference copy; no build infrastructure (package.json, Makefile, lockfiles) is included. The original project uses **Bun** as bundler and runtime, **TypeScript** as the language, and **React + Ink** for terminal UI.

Because there is no build system in this repo, you cannot compile or run tests directly. Treat this as a read-only reference codebase.

---

## Architecture overview

### Entry point and initialization

`src/main.tsx` is the CLI entry point (~4 600 lines). It handles:
- OAuth / keychain prefetch (parallel)
- MDM / settings loading in background
- Command, plugin, skill, and MCP server registration
- GrowthBook feature flag initialization
- Launching the REPL (`App.tsx`) or executing a one-shot command

### Message processing loop

`src/query.ts` (~68 KB) is the core conversation loop:

```
User input → assemble tool pool → call Claude API (streaming)
          → parse tool calls → execute tools → feed results back
          → repeat until assistant finishes or user interrupts
```

`src/QueryEngine.ts` (~46 KB) wraps `query.ts` with state management.

### Tool system

```
getAllBaseTools()       ← master list; imports gated by build-time feature() flags
      ↓
  getTools()           ← filter by deny-rules, REPL mode, each tool's isEnabled()
      ↓
assembleToolPool()     ← getTools() + MCP tools (deduped, stably sorted for prompt cache)
```

Key source files: `src/tools.ts`, `src/constants/tools.ts`.

Each tool lives in `src/tools/{ToolName}Tool/` and exposes: `execute()`, `schema`, `isEnabled()`, `validate()`.

**Tool gating has three layers:**
1. **Build-time** — `feature('FLAG')` resolved by Bun at bundle time (e.g. `KAIROS`, `AGENT_TRIGGERS`, `MONITOR_TOOL`, `COORDINATOR_MODE`)
2. **Runtime env vars** — `USER_TYPE === 'ant'` adds internal-only tools; `CLAUDE_CODE_SIMPLE=true` reduces to `[Bash, Read, Edit]`; `ENABLE_LSP_TOOL=true` adds LSP
3. **Per-tool `isEnabled()`** — checked each time `getTools()` runs

**Context-specific tool subsets** (defined in `src/constants/tools.ts`):
- Interactive session → full `getTools()` + MCP tools
- Async agent (spawned by Agent tool) → `ASYNC_AGENT_ALLOWED_TOOLS`: Read, WebSearch, TodoWrite, Grep, WebFetch, Glob, Bash/PowerShell, Edit, Write, NotebookEdit, Skill, StructuredOutput, ToolSearch, EnterWorktree, ExitWorktree
- In-process teammate → above + TaskCreate/Get/List/Update, SendMessage, CronCreate/Delete/List
- Coordinator mode → only: Agent, TaskStop, SendMessage, StructuredOutput
- SDK/non-interactive → `getTools()` + `StructuredOutput` appended

### Deferred tool schemas (ToolSearch)

To save prompt tokens, some tools are *deferred*: the model only sees their names (via `<system-reminder>` or `<available-deferred-tools>` block), not their full JSON schema. The model calls `ToolSearch` to load schemas on demand. The Anthropic API server then injects the full schema into model context via a `tool_reference` content block — this mechanism only works with Anthropic's 1P API.

Tools that are always deferred: MCP tools, any tool with `shouldDefer: true`.
Tools that are never deferred: `ToolSearch` itself, `Brief`, `Agent` (when spawning subagents), `SendUserFile`.

After context compaction, discovered tools are preserved in `compactMetadata.preCompactDiscoveredTools`.

### State management

`src/state/AppState.tsx` + `src/state/AppStateStore.ts` — Zustand-like reactive store. Components consume state via hooks; listeners registered via `src/state/onChangeAppState.ts`. State can be serialized for session resume and teleport.

### Commands

`src/commands.ts` registers 100+ CLI commands from `src/commands/` (one module per command). Commander.js handles CLI argument parsing.

### Bridge / remote sessions

`src/bridge/` — remote session management: WebSocket/SSH tunneling, JWT auth, trusted devices, inbound message and attachment handling. Key files: `bridgeMain.ts`, `replBridge.ts`, `createSession.ts`, `sessionRunner.ts`.

### Services

`src/services/` contains: API client + message normalization, GrowthBook analytics, MCP server integration, message compaction, LSP, plugin/skill installation, OAuth (multiple providers), rate-limit and cost tracking.

### Terminal UI

`src/ink/` — custom terminal React renderer (fork/adaptation of the Ink library).
`src/components/` — 146+ React components (App.tsx root, dialogs, progress bars, themed primitives).

### Tasks

`src/Task.ts` + `src/tasks/` — task abstraction for local bash, local agents, remote agents, teammates, and workflows. Tasks stream output to disk, support abort, and report lifecycle state (pending → running → completed/failed/killed).

### Multi-agent architecture (two independent systems)

**System 1 — Background Agents** (available to all users): AgentTool spawns isolated subagents. Three lifecycle tools: `AgentTool` (spawn), `TaskOutput` (poll result from disk), `TaskStop` (kill). Workers get a restricted tool subset (`ASYNC_AGENT_ALLOWED_TOOLS`) — notably AgentTool itself is blocked to prevent recursion.

**System 2 — Agent Swarms / Teams** (experimental, not yet public): Multiple Claude Code instances collaborate on a shared task list stored at `~/.claude/tasks/<teamName>/`. Gated by `isAgentSwarmsEnabled()` which requires opt-in flag **and** GrowthBook gate `tengu_amber_flint` (server-side). Two spawn backends: tmux pane (separate OS process) or in-process (AsyncLocalStorage isolation). Teammates get 4 additional coordination tools: `TaskCreate`, `TaskGet`, `TaskList`, `TaskUpdate`. Cross-team communication uses `SendMessage`.

The `AgentTool` wire name was formerly `Task` (see `LEGACY_AGENT_TOOL_NAME`) — relevant when reading old hook/permission configs.

See `docs/multi-agent.md` for full breakdown.

---

## Notable internal conventions

- `feature('FLAG_NAME')` — Bun build-time macro; evaluates to `true`/`false` at bundle time. Do not treat as a runtime function.
- `process.env.USER_TYPE === 'ant'` — Anthropic-internal environment; unlocks tools like `Config`, `Tungsten`, `REPL`, `SuggestBackgroundPR`, and nested Agent calls.
- Several tools referenced in `src/tools.ts` are **not present in this repo** (gated by flags not included in the leak): `PushNotificationTool`, `SendUserFileTool`, `WebBrowserTool`, `WorkflowTool`, `MonitorTool`, `SnipTool`, `CtxInspectTool`, `TerminalCaptureTool`, `TungstenTool`, `VerifyPlanExecutionTool`, `ListPeersTool`.
- `GlobTool` and `GrepTool` are hidden in Anthropic's native ant build — replaced by embedded `bfs`/`ugrep` binaries for performance.
- Linter: Biome (seen in `biome-ignore-all` comments).
