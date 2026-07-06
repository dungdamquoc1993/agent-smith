# Resources

Catalog/config layer for definitions from memory, plugins, or Postgres.

This package owns resource records, versions, store protocols, memory/Postgres stores,
and resolving catalog records into runtime snapshots.

## Boundary

```
ResourceStore(s)
  -> ResourceResolver
  -> AgentHarnessResources + AgentDefinition list + MCP configs
```

`AgentHarness` receives only the resolved `AgentHarnessResources` snapshot. It does not know which
store produced the resources.

`PostgresResourceStore` is only a catalog adapter. Authorization, principal ownership, project
membership, and ACL policy are intentionally outside v1; callers should choose the right store
context before resolving resources.

## Resource Kinds

| Kind | Meaning |
|------|---------|
| `skill` | Specialized instructions loaded into harness resources |
| `prompt_template` | Reusable prompt body for `prompt_from_template()` |
| `agent_definition` | Persistable blueprint for spawning a harness-backed agent |
| `mcp_server_config` | Configuration for making MCP capabilities available |

Runtime state such as tasks, todos, sleeps, agent runs, and pending user questions belongs outside
this catalog.

## Postgres V1

`PostgresResourceStore` persists the same `ResourceStore` contract into generic `resources` and
`resource_versions` tables. It manages one catalog scope per instance, defaulting to `user`; create
separate store instances when a resolver needs priority layers such as `project < user < session`.
