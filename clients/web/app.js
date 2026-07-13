const STORAGE_KEY = "agent-smith.hris-sandbox.state";

const fakeUsers = [
  {
    id: "hris-employee-001",
    employeeId: "E001",
    displayName: "Nguyen Van An",
    email: "an.nguyen@example.test",
    department: "Engineering",
    title: "Software Engineer",
    managerId: "M010",
    location: "Ho Chi Minh City",
    roles: ["employee"],
  },
  {
    id: "hris-manager-010",
    employeeId: "M010",
    displayName: "Tran Minh Chau",
    email: "chau.tran@example.test",
    department: "Engineering",
    title: "Engineering Manager",
    managerId: "D100",
    location: "Ho Chi Minh City",
    roles: ["manager", "employee"],
  },
  {
    id: "hris-admin-100",
    employeeId: "H100",
    displayName: "Le Thu Ha",
    email: "ha.le@example.test",
    department: "People Operations",
    title: "HR Admin",
    managerId: "C001",
    location: "Hanoi",
    roles: ["hr_admin", "employee"],
  },
];

const state = {
  user: null,
  smithSessionId: null,
  externalSessionId: crypto.randomUUID(),
  modelKey: null,
  busy: false,
};

const userList = document.querySelector("#userList");
const currentUser = document.querySelector("#currentUser");
const messages = document.querySelector("#messages");
const eventList = document.querySelector("#eventList");
const promptForm = document.querySelector("#promptForm");
const promptInput = document.querySelector("#promptInput");
const sendButton = document.querySelector("#sendButton");
const modelSelect = document.querySelector("#modelSelect");
const sessionLabel = document.querySelector("#sessionLabel");
const newSessionButton = document.querySelector("#newSessionButton");
const signOutButton = document.querySelector("#signOutButton");
const clearEventsButton = document.querySelector("#clearEventsButton");

loadState();
render();
loadModels();

modelSelect.addEventListener("change", () => {
  state.modelKey = modelSelect.value || null;
  saveState();
});

promptForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  const prompt = promptInput.value.trim();
  if (!prompt || state.busy) return;
  promptInput.value = "";
  await sendPrompt(prompt);
});

newSessionButton.addEventListener("click", () => {
  state.smithSessionId = null;
  state.externalSessionId = crypto.randomUUID();
  messages.replaceChildren();
  addEvent("client.session_reset", { externalSessionId: state.externalSessionId });
  saveState();
  render();
});

signOutButton.addEventListener("click", () => {
  state.user = null;
  state.smithSessionId = null;
  state.externalSessionId = crypto.randomUUID();
  messages.replaceChildren();
  saveState();
  render();
});

clearEventsButton.addEventListener("click", () => {
  eventList.replaceChildren();
});

async function sendPrompt(prompt) {
  if (!state.user) {
    addEvent("client.sign_in_required", { message: "Choose a fake HRIS user first." });
    return;
  }
  setBusy(true);
  appendMessage("user", prompt);
  const assistantNode = appendMessage("assistant", "");
  let assistantText = "";

  try {
    const response = await fetch("/api/oneai/chat/stream", {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({
        prompt,
        smithSessionId: state.smithSessionId,
        externalSessionId: state.externalSessionId,
        modelKey: state.modelKey || modelSelect.value,
        user: state.user,
        userAgent: navigator.userAgent,
        timezone: Intl.DateTimeFormat().resolvedOptions().timeZone,
        locale: navigator.language,
      }),
    });

    if (!response.ok || !response.body) {
      const detail = await response.text();
      throw new Error(detail || `HTTP ${response.status}`);
    }

    await readSse(response.body, (event) => {
      addEvent(event.name, event.data);
      const payload = event.data && typeof event.data === "object" ? event.data : {};
      const eventName = payload.event || event.name;
      const data = payload.data || payload;

      if (eventName === "session.resolved" && data.id) {
        state.smithSessionId = data.id;
        saveState();
        render();
      }
      if (eventName === "message.delta" && typeof data.text === "string") {
        assistantText += data.text;
        updateMessage(assistantNode, assistantText);
      }
      if (eventName === "run.completed") {
        const finalText = data.finalText || assistantText;
        updateMessage(assistantNode, finalText);
      }
      if (eventName === "run.failed") {
        updateMessage(assistantNode, data.message || "Run failed.");
        assistantNode.classList.add("failed");
      }
    });
  } catch (error) {
    updateMessage(assistantNode, error.message || "Request failed.");
    assistantNode.classList.add("failed");
    addEvent("client.error", { message: error.message || String(error) });
  } finally {
    setBusy(false);
  }
}

async function loadModels() {
  try {
    const response = await fetch("/api/oneai/models");
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    const payload = await response.json();
    const models = Array.isArray(payload.models) ? payload.models : [];
    modelSelect.replaceChildren(
      ...models.map((model) => {
        const option = document.createElement("option");
        option.value = model.key;
        option.textContent = model.label;
        return option;
      })
    );
    const defaultKey = payload.defaults?.modelKey || models[0]?.key || null;
    const selectedKey = models.some((model) => model.key === state.modelKey)
      ? state.modelKey
      : defaultKey;
    state.modelKey = selectedKey;
    modelSelect.value = selectedKey || "";
    modelSelect.disabled = models.length === 0;
    saveState();
    render();
  } catch (error) {
    const option = document.createElement("option");
    option.value = "";
    option.textContent = "Models unavailable";
    modelSelect.replaceChildren(option);
    modelSelect.disabled = true;
    addEvent("client.models_unavailable", { message: error.message || String(error) });
  }
}

async function readSse(stream, onEvent) {
  const reader = stream.getReader();
  const decoder = new TextDecoder();
  let buffer = "";

  while (true) {
    const { value, done } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    const chunks = buffer.split(/\n\n/);
    buffer = chunks.pop() || "";
    for (const chunk of chunks) {
      const parsed = parseSseChunk(chunk);
      if (parsed) onEvent(parsed);
    }
  }
  if (buffer.trim()) {
    const parsed = parseSseChunk(buffer);
    if (parsed) onEvent(parsed);
  }
}

function parseSseChunk(chunk) {
  let name = "message";
  const dataLines = [];
  for (const line of chunk.split(/\n/)) {
    if (line.startsWith("event:")) name = line.slice(6).trim();
    if (line.startsWith("data:")) dataLines.push(line.slice(5).trimStart());
  }
  if (!dataLines.length) return null;
  const rawData = dataLines.join("\n");
  try {
    return { name, data: JSON.parse(rawData) };
  } catch {
    return { name, data: rawData };
  }
}

function render() {
  userList.replaceChildren(
    ...fakeUsers.map((user) => {
      const button = document.createElement("button");
      button.type = "button";
      button.className = user.id === state.user?.id ? "user-card selected" : "user-card";
      button.innerHTML = `
        <strong>${escapeHtml(user.displayName)}</strong>
        <span>${escapeHtml(user.title)}</span>
        <small>${escapeHtml(user.department)} - ${escapeHtml(user.employeeId)}</small>
      `;
      button.addEventListener("click", () => {
        state.user = user;
        state.smithSessionId = null;
        state.externalSessionId = crypto.randomUUID();
        messages.replaceChildren();
        addEvent("client.user_selected", { user: user.displayName });
        saveState();
        render();
      });
      return button;
    })
  );

  currentUser.innerHTML = state.user
    ? `
      <strong>${escapeHtml(state.user.displayName)}</strong>
      <span>${escapeHtml(state.user.email)}</span>
      <small>${escapeHtml(state.user.roles.join(", "))}</small>
    `
    : `
      <strong>Not signed in</strong>
      <span>Choose a fake HRIS user below.</span>
      <small>Assertions are signed by the local relay.</small>
    `;
  sessionLabel.textContent = !state.user
    ? "Sign in to start a Smith session"
    : state.smithSessionId
      ? `Smith session ${state.smithSessionId}`
      : `External session ${state.externalSessionId}`;
  sendButton.disabled = state.busy;
  promptInput.disabled = state.busy || !state.user;
  signOutButton.disabled = !state.user;
}

function appendMessage(role, text) {
  const node = document.createElement("article");
  node.className = `message ${role}`;
  node.innerHTML = `<span>${role}</span><p></p>`;
  updateMessage(node, text);
  messages.appendChild(node);
  messages.scrollTop = messages.scrollHeight;
  return node;
}

function updateMessage(node, text) {
  const paragraph = node.querySelector("p");
  paragraph.textContent = text || " ";
  messages.scrollTop = messages.scrollHeight;
}

function addEvent(name, data) {
  const item = document.createElement("li");
  item.innerHTML = `
    <span>${escapeHtml(name)}</span>
    <code>${escapeHtml(JSON.stringify(data, null, 2))}</code>
  `;
  eventList.prepend(item);
}

function setBusy(value) {
  state.busy = value;
  sendButton.textContent = value ? "Sending" : "Send";
  render();
}

function loadState() {
  try {
    const saved = JSON.parse(localStorage.getItem(STORAGE_KEY) || "{}");
    const savedUser = fakeUsers.find((user) => user.id === saved.userId);
    if (savedUser) state.user = savedUser;
    if (typeof saved.smithSessionId === "string") state.smithSessionId = saved.smithSessionId;
    if (typeof saved.externalSessionId === "string") state.externalSessionId = saved.externalSessionId;
    if (typeof saved.modelKey === "string") state.modelKey = saved.modelKey;
  } catch {
    localStorage.removeItem(STORAGE_KEY);
  }
}

function saveState() {
  localStorage.setItem(
    STORAGE_KEY,
    JSON.stringify({
      userId: state.user?.id || null,
      smithSessionId: state.smithSessionId,
      externalSessionId: state.externalSessionId,
      modelKey: state.modelKey,
    })
  );
}

function escapeHtml(value) {
  return String(value)
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}
