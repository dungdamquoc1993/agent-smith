# Harness Learning Notes

Tai lieu nay la so tay hoc dan ve `packages/agent/src/harness`.
Muc tieu khong phai document API day du ngay tu dau, ma ghi lai cac mental model da hieu khi doc code.

Vi tri hien tai cua nguoi doc:

- Da doc va nam so bo `ai`, `agent-loop`, `agent`.
- Dang bat dau voi `harness/types.ts`.
- Hieu tam thoi: `AgentHarness` giong mot agent instance day du hon, bao quanh `agent-loop` va them session, env, resources, hooks, queues, compaction.

## 1. AgentHarness La Gi?

`agent-loop` la dong co vong lap:

```text
messages -> provider/model -> assistant response -> tool calls -> tool results -> turn end
```

`AgentHarness` la runtime shell bao quanh dong co do:

```text
model hien tai
thinking level
active tools
session/history
execution env
resources: skills/templates
stream/auth options
queues: steer/followUp/nextTurn
events/hooks
compaction/tree navigation
```

Vay co the hieu mot `AgentHarness` instance gan nhu la mot phien agent/coding-agent dang song.
No khong phai model rieng, ma la lop dieu phoi runtime cho model va agent loop.

## 2. ExecutionEnv

Trong `types.ts`, `ExecutionEnv` la:

```ts
interface ExecutionEnv extends FileSystem, Shell {}
```

Nghia la harness can mot moi truong co kha nang:

- thao tac filesystem: read/write/list/remove/temp file...
- chay shell command: `exec(command)`

Diem quan trong: harness khong tu hard-code Node `fs` hay `child_process`.
No chi nhan mot abstraction.

Ly do abstraction nay nam trong harness:

- Harness la runtime boundary, noi ghep model, tools, session va environment.
- Coding agent can doc/ghi file va chay lenh, nhung moi truong co the la local machine, sandbox, container remote, test fake env...
- Vi vay harness noi: "toi can mot env co cac capability nay", thay vi noi: "toi se tu goi fs/process truc tiep".

`FileSystem` va `Shell` tra ve `Result` de expected failures duoc encode bang `{ ok: false, error }`, khong nem exception lung tung.

## 3. Session Entry

Session history cua harness khong chi la mang messages.
No la mot cay lich su gom nhieu loai `entry`.

Mot `entry` co the la:

- `message`
- `model_change`
- `thinking_level_change`
- `active_tools_change`
- `compaction`
- `branch_summary`
- `custom`
- `custom_message`
- `label`
- `session_info`
- `leaf`

Dung tu `entry` vi moi record trong lich su khong nhat thiet la chat message.
No la mot node/event trong session tree.

Moi entry co:

```ts
id: string;
parentId: string | null;
timestamp: string;
type: string;
```

Khi append entry moi, `parentId` thuong tro ve leaf hien tai.
Vi vay session co the tao thanh branch/tree, khong chi la mot list tuyen tinh.

Khi can dung context cho model, `session.buildContext()` lay path tu leaf ve root, roi replay cac entry tren branch do:

- message nao duoc dua vao context
- model hien tai la gi
- thinking level hien tai la gi
- active tools hien tai la gi
- compaction summary nao dang co hieu luc

## 4. Session, Storage, Repo

Ba tang nay nen nho ngan gon:

```text
SessionStorage
  = luu tru cho 1 session
  = memory hoac jsonl

Session
  = API/logic thao tac tren 1 session
  = appendMessage, buildContext, moveTo...

SessionRepo
  = quan ly nhieu session
  = create/open/list/delete/fork
```

Cu the:

```text
InMemorySessionStorage
  = 1 session luu trong RAM

JsonlSessionStorage
  = 1 session luu trong 1 file .jsonl

InMemorySessionRepo
  = nhieu session trong Map

JsonlSessionRepo
  = nhieu session trong nhieu file .jsonl
```

`Session` duoc tao bang cach boc quanh mot storage:

```ts
const storage = new InMemorySessionStorage(...);
const session = new Session(storage);
```

Vi vay storage la noi giu data/state, con `Session` la lop domain API.
Repo nam ngoai de quan ly nhieu `Session`.

## 5. PendingSessionWrite

`pendingSessionWrites` la buffer tam thoi trong mot `AgentHarness` instance.
No khong cross-session va khong phai long-term memory.

No duoc dung khi harness dang ban, vi du dang o phase `turn`, ma co mutation can ghi vao session:

- doi model
- doi thinking level
- doi active tools
- append external/custom message
- append label/session info/custom entry

Neu harness dang `idle`, mutation duoc ghi ngay vao session.
Neu harness khong `idle`, mutation duoc dua vao `pendingSessionWrites`.

Vi du mental model:

```text
setModel(newModel)
  -> neu idle: append model_change vao session ngay
  -> neu dang chay turn: push model_change vao pendingSessionWrites
  -> cap nhat this.model ngay lap tuc
```

Nghia la state in-memory cua harness co the doi ngay, nhung session history se duoc commit o boundary sach.

Nhung diem flush chinh:

- `turn_end`: flush pending writes, roi emit `save_point`
- `agent_end`: flush lan nua, roi emit `settled`
- `executeTurn finally`: flush phong truong hop ket thuc bat thuong

Ket luan: `pendingSessionWrites` khong lien quan truc tiep den luong reasoning cua agent.
No la hang cho commit session history/state metadata.

## 6. Queues: steer, followUp, nextTurn

Queues khac voi `pendingSessionWrites`.
Queues chua `AgentMessage` se duoc dua vao agent flow.

### steer

`steer` la message chen vao khi agent dang chay.
Sau khi agent hoan thanh mot turn/tool batch, `agent-loop` hoi `getSteeringMessages()`.
Neu co steer messages, no dua vao context va goi model tiep.

### followUp

`followUp` la message chi duoc dung khi agent "would stop".

`agent-loop` khong doan truoc model sap ket thuc.
No chi biet sau khi da:

1. goi model xong
2. xu ly tool calls neu co
3. thay khong con tool calls de tiep tuc
4. thay khong co steering messages

Luc do binh thuong agent se stop.
Ngay truoc khi stop that, loop hoi `getFollowUpMessages()`.
Neu co follow-up, no dua vao pending messages va tiep tuc chay.

### nextTurn

`nextTurn` khac `followUp`.

- `followUp`: dung trong current run, khi agent sap dung.
- `nextTurn`: de danh cho lan `prompt()` ke tiep.

Khi `executeTurn()` bat dau, harness drain `nextTurnQueue` va prepend cac message do vao prompt moi.

## 7. Events Va Hooks

Harness co event/hook rieng, ngoai event/hook cua `agent-loop`.

`agent-loop` chi biet vong lap ben trong:

```text
context
provider call
assistant message
tool call
tool result
turn end
```

Harness biet ca runtime bao ngoai:

```text
session tree
queue
resources/skills/templates
provider request options
provider payload/response
model/tools/thinking state
compaction
branch navigation
save point / settled
```

Co hai cach dung:

```ts
harness.subscribe((event) => {
  // observe moi event
});
```

Dung cho UI/logging/analytics/state display.

```ts
harness.on("tool_call", (event) => {
  // hook co the tra result de can thiep
});
```

Dung de can thiep vao lifecycle.

Mot so hook co the tac dong:

- `before_agent_start`: them messages hoac doi system prompt.
- `context`: transform messages truoc khi goi provider.
- `before_provider_request`: patch stream options/header/metadata.
- `before_provider_payload`: sua raw payload.
- `tool_call`: block tool.
- `tool_result`: sua tool result hoac terminate.
- `session_before_compact`: cancel hoac cung cap compaction san.
- `session_before_tree`: cancel hoac cung cap branch summary.

Ket luan: hooks cua harness ton tai vi hooks cua `agent-loop` khong du thong tin ve session/resources/runtime state.

## 8. AgentHarness Class Notes

Nhung diem da hoc khi doc `agent-harness.ts`:

### subscribe vs on

Ca hai deu la cach ben ngoai dang ky callback vao `this.handlers`, nhung khac chieu du lieu:

```text
subscribe = thong bao
on        = hoi y kien / cho can thiep
```

`subscribe(...)` nghe event broadcast:

```text
harness -> ben ngoai
```

Dung cho UI/logging/render state.

`on(type, handler)` la hook co the return result:

```text
harness -> ben ngoai -> harness
```

Dung de can thiep flow, vi du block tool, sua context, sua tool result.

### emitOwn, emitAny, emitHook

```text
emitOwn  = broadcast event do harness tu tao
emitAny  = broadcast event cua harness hoac event tu agent-loop
emitHook = goi handler tu on(...) va lay return value
```

Ben ngoai khong duoc tu emit event.
Ben ngoai chi dang ky bang `subscribe` hoac `on`; harness quyet dinh luc nao event/hook duoc goi.

### Bridge Sang Agent Loop

Nhom method nay la adapter giua harness va `agent-loop`:

```text
createTurnState     = snapshot harness state
createContext       = turnState -> AgentContext
createStreamFn      = provider call co auth/hooks/options
createLoopConfig    = dua hooks/queues vao AgentLoopConfig
drainQueuedMessages = lay steer/followUp messages cho loop
```

`createTurnState()` lay state tu session/resources/model/tools/thinking/stream options.
`createLoopConfig()` la noi harness cam cac hook nhu `context`, `tool_call`, `tool_result`, `prepareNextTurn`, `getSteeringMessages`, `getFollowUpMessages` vao `agent-loop`.

### flushPendingSessionWrites

`flushPendingSessionWrites()` xa pending writes vao session storage tai boundary an toan.
No khong phai "event loop ranh thi ghi", ma ghi o cac diem nhu:

```text
turn_end
agent_end
prepareNextTurn
executeTurn finally
```

### handleAgentEvent

`handleAgentEvent()` la cau noi tu `agent-loop` ve harness:

```text
nhan AgentEvent
-> ghi session neu can
-> flush pending writes neu den save point
-> forward event cho UI/app qua subscribe
```

Vi du:

```text
message_end -> session.appendMessage
turn_end    -> flushPendingSessionWrites + save_point
agent_end   -> flush + phase idle + settled
```

### executeTurn

`executeTurn()` trong harness khong phai mot lan provider/model call.
No gan voi mot lan user submit / mot lan public run:

```text
prompt / skill / promptFromTemplate
  -> executeTurn
  -> runAgentLoop
  -> co the gom nhieu model calls va tool calls
```

Mot `executeTurn()` co the chua nhieu "turn" nho ben trong `agent-loop`.

### Skill Invocation

Trong harness co hai kieu skill:

```text
resources.skills
  = skill catalog, co the dua vao system prompt de model tu quyet dinh

harness.skill(name)
  = explicit invocation tu ben ngoai
  = format full skill thanh prompt roi goi executeTurn
```

Vay `harness.skill()` khong phai agent tu chon skill.
No la app/user ep goi mot skill cu the.

### navigateTree

`navigateTree(targetId)` la API tong quat de doi leaf trong session tree.
No dung duoc cho ca:

```text
1. switch branch/history
2. edit message cu va fork tu do
```

Neu target la user/custom message:

```text
leaf = parent cua message do
return editorText
```

UI dua `editorText` vao editor; user sua roi submit se tao nhanh moi.

Neu target la entry khac:

```text
leaf = targetId
```

Neu `summarize: true`, harness co the tom tat branch cu truoc khi roi di.

## 9. Tam Thoi Can Nho

Doc `harness/types.ts` khong nen doc nhu mot list type roi rac.
Nen doc no nhu ban hop dong cong khai cua agent runtime:

```text
Result/Error
Resources
ExecutionEnv
Session Tree
Phase/Pending Writes/Queues
Events/Hooks
Compaction/Tree Navigation
Constructor Options
```

`AgentHarness` = lop dieu phoi runtime.
`agent-loop` = dong co thuc thi vong lap agent.
`session` = lich su persist dang tree.
`pendingSessionWrites` = buffer commit session state ngan han.
`queues` = message flow cua user chen vao agent.
`events/hooks` = API de app observe/can thiep vao runtime.
