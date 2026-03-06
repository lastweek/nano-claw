const sessionList = document.getElementById("session-list");
const sessionTitle = document.getElementById("session-title");
const sessionSummary = document.getElementById("session-summary");
const transcript = document.getElementById("transcript");
const activityLog = document.getElementById("activity-log");
const turnLinks = document.getElementById("turn-links");
const turnStatus = document.getElementById("run-status");
const promptForm = document.getElementById("prompt-form");
const promptInput = document.getElementById("prompt-input");
const submitButton = document.getElementById("submit-button");
const newSessionButton = document.getElementById("new-session-button");
const closeSessionButton = document.getElementById("close-session-button");

let currentSessionId = null;
let currentEventSource = null;
let activeAssistantElement = null;
let selectedSessionState = "active";

function appendActivity(text) {
  const entry = document.createElement("div");
  entry.className = "activity-entry";
  entry.textContent = text;
  activityLog.appendChild(entry);
  activityLog.scrollTop = activityLog.scrollHeight;
}

function appendMessage(role, content) {
  const message = document.createElement("div");
  message.className = `message ${role}`;
  message.textContent = content;
  transcript.appendChild(message);
  transcript.scrollTop = transcript.scrollHeight;
  return message;
}

function resetTurnStream() {
  if (currentEventSource) {
    currentEventSource.close();
    currentEventSource = null;
  }
  activeAssistantElement = null;
}

function refreshComposerState() {
  const closed = selectedSessionState !== "active";
  submitButton.disabled = closed;
  promptInput.disabled = closed;
}

async function fetchJson(url, options = {}) {
  const response = await fetch(url, options);
  if (!response.ok) {
    let detail = response.statusText;
    try {
      const payload = await response.json();
      detail = payload.detail || detail;
    } catch (_error) {
      // Ignore response parse errors.
    }
    throw new Error(detail);
  }
  if (response.status === 204) {
    return null;
  }
  return response.json();
}

async function loadSessions() {
  const sessions = await fetchJson("/api/v1/sessions");
  sessionList.innerHTML = "";

  sessions.forEach((session) => {
    const item = document.createElement("button");
    item.type = "button";
    item.className = `session-item${session.id === currentSessionId ? " active" : ""}`;
    const stateSuffix = session.state === "closed" ? " (closed)" : "";
    item.innerHTML = `<h3>${session.title}${stateSuffix}</h3><p>${session.summary_text || "No summary yet."}</p>`;
    item.addEventListener("click", () => selectSession(session.id));
    sessionList.appendChild(item);
  });

  if (!currentSessionId && sessions.length > 0) {
    await selectSession(sessions[0].id);
  }
}

async function selectSession(sessionId) {
  resetTurnStream();
  currentSessionId = sessionId;
  const session = await fetchJson(`/api/v1/sessions/${sessionId}`);
  selectedSessionState = session.state;

  sessionTitle.textContent = session.title;
  sessionSummary.textContent = session.summary_text || "No summary yet.";
  turnStatus.textContent = session.busy ? "busy" : session.state;
  transcript.innerHTML = "";
  activityLog.innerHTML = "";
  turnLinks.innerHTML = "";

  session.messages.forEach((message) => {
    appendMessage(message.role, message.content);
  });

  session.recent_turns.forEach((turn) => {
    appendActivity(`[${turn.status}] ${turn.input_text}`);
  });

  refreshComposerState();
  await loadSessions();
}

function attachStream(turn) {
  resetTurnStream();
  turnStatus.textContent = turn.status;
  currentEventSource = new EventSource(turn.stream_url);
  activeAssistantElement = appendMessage("assistant", "");
  appendActivity(`turn ${turn.id} queued`);

  currentEventSource.addEventListener("status", (event) => {
    const payload = JSON.parse(event.data);
    turnStatus.textContent = payload.payload.status;
    appendActivity(`status: ${payload.payload.status}`);
  });

  currentEventSource.addEventListener("chunk", (event) => {
    const payload = JSON.parse(event.data);
    if (activeAssistantElement) {
      activeAssistantElement.textContent += payload.payload.text;
      transcript.scrollTop = transcript.scrollHeight;
    }
  });

  currentEventSource.addEventListener("done", async (event) => {
    const payload = JSON.parse(event.data);
    turnStatus.textContent = "completed";
    if (activeAssistantElement && !activeAssistantElement.textContent) {
      activeAssistantElement.textContent = payload.payload.final_output;
    }
    appendActivity("turn completed");
    currentEventSource.close();
    currentEventSource = null;
    await refreshTurnLinks(turn.id);
    await selectSession(currentSessionId);
  });

  currentEventSource.addEventListener("error", async (event) => {
    if (!event.data) {
      return;
    }
    const payload = JSON.parse(event.data);
    turnStatus.textContent = "failed";
    appendActivity(`error: ${payload.payload.message}`);
    currentEventSource.close();
    currentEventSource = null;
    await refreshTurnLinks(turn.id);
    await selectSession(currentSessionId);
  });
}

async function refreshTurnLinks(turnId) {
  await fetchJson(`/api/v1/turns/${turnId}`);
  turnLinks.innerHTML = "";
  const apiLinks = [["Turn", `/api/v1/turns/${turnId}`]];
  apiLinks.forEach(([label, href]) => {
    const anchor = document.createElement("a");
    anchor.href = href;
    anchor.textContent = label;
    anchor.target = "_blank";
    turnLinks.appendChild(anchor);
  });
}

promptForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  if (!currentSessionId) {
    appendActivity("create a session first");
    return;
  }
  if (selectedSessionState !== "active") {
    appendActivity("session is closed");
    return;
  }

  const input = promptInput.value;
  if (!input.trim()) {
    appendActivity("prompt cannot be empty");
    return;
  }

  submitButton.disabled = true;
  appendMessage("user", input);
  try {
    const turn = await fetchJson(`/api/v1/sessions/${currentSessionId}/turns`, {
      method: "POST",
      headers: {"Content-Type": "application/json"},
      body: JSON.stringify({input}),
    });
    promptInput.value = "";
    attachStream(turn);
  } catch (error) {
    appendActivity(`request failed: ${error.message}`);
  } finally {
    submitButton.disabled = false;
  }
});

newSessionButton.addEventListener("click", async () => {
  const title = window.prompt("Session title (optional)");
  try {
    const session = await fetchJson("/api/v1/sessions", {
      method: "POST",
      headers: {"Content-Type": "application/json"},
      body: JSON.stringify({title}),
    });
    await loadSessions();
    await selectSession(session.id);
  } catch (error) {
    appendActivity(`session creation failed: ${error.message}`);
  }
});

closeSessionButton.addEventListener("click", async () => {
  if (!currentSessionId) {
    return;
  }
  try {
    await fetchJson(`/api/v1/sessions/${currentSessionId}`, {method: "DELETE"});
    appendActivity(`session ${currentSessionId} closed`);
    selectedSessionState = "closed";
    refreshComposerState();
    await loadSessions();
    await selectSession(currentSessionId);
  } catch (error) {
    appendActivity(`session close failed: ${error.message}`);
  }
});

loadSessions().catch((error) => {
  appendActivity(`startup failed: ${error.message}`);
});
