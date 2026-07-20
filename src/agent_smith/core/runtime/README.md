# Runtime

Execution layer between resource definitions, concrete harness instances, and run recording.

`AgentDefinition` is a persisted blueprint. `AgentRuntime` resolves that blueprint through the
resource plane and a `ToolRegistry`, builds a real `AgentHarness`, and exposes the standard
`execute()` path that records the run and every provider call.

## Boundary

```
AgentDefinition
  + ResourceResolver
  + ToolRegistry
  + session supplied by caller
  -> AgentRuntimeSpec
  -> AgentHarnessOptions
  -> AgentHarness
  -> AgentExecutionResult
```

The caller owns session creation/forking and adapts runtime events to its transport. `AgentRuntime`
owns execution lifecycle and recording; `AgentHarness` remains focused on turn execution, session
writes, hooks, queues, and compaction.
