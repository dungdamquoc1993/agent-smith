# Runtime

Assembly layer between resource definitions and concrete harness instances.

`AgentDefinition` is a persisted blueprint. `AgentFactory` resolves that blueprint through the
resource plane and a `ToolRegistry`, then builds `AgentHarnessOptions` for a real `AgentHarness`.

## Boundary

```
AgentDefinition
  + ResourceResolver
  + ToolRegistry
  + session supplied by caller
  -> AgentRuntimeSpec
  -> AgentHarnessOptions
  -> AgentHarness
```

The caller still owns session creation/forking and agent-run state. This keeps `AgentHarness` focused
on turn execution, session writes, hooks, queues, and compaction.
