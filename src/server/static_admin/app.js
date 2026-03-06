const ROOT_DEFS = [
  {id: "overview", kind: "overview", label: "Overview", hasChildren: false},
  {id: "sessions-root", kind: "sessions-root", label: "Sessions", hasChildren: true},
  {id: "turns-root", kind: "turns-root", label: "Global Turns", hasChildren: true},
  {id: "event-bus", kind: "event-bus", label: "Event Bus", hasChildren: false},
  {id: "config", kind: "config", label: "Config", hasChildren: false},
];

const SESSION_CHILD_NODE_DEFS = [
  {kind: "session-detail", label: "Session", hasChildren: false},
  {kind: "context", label: "Context", hasChildren: false},
  {kind: "runtime", label: "Runtime", hasChildren: false},
  {kind: "agent", label: "Agent", hasChildren: false},
  {kind: "memory", label: "Memory", hasChildren: true},
  {kind: "skills", label: "Skills", hasChildren: true},
  {kind: "tools", label: "Tools", hasChildren: true},
  {kind: "mcp", label: "MCP", hasChildren: false},
  {kind: "subagents", label: "Subagents", hasChildren: false},
  {kind: "turns", label: "Turns", hasChildren: true},
  {kind: "logs", label: "Logs", hasChildren: true},
];

const STREAM_RESOURCES = "overview,sessions,runtimes,turns,event-bus,config";
const DETAIL_TABS = ["summary", "related", "raw"];
const ROOT_KINDS = new Set(ROOT_DEFS.map((item) => item.kind));

const elements = {
  adminNav: document.getElementById("admin-nav"),
  treeView: document.getElementById("tree-view"),
  detailHeader: document.getElementById("detail-header"),
  detailTabs: document.getElementById("detail-tabs"),
  detailContent: document.getElementById("detail-content"),
  refreshButton: document.getElementById("refresh-button"),
  connectionStatus: document.getElementById("connection-status"),
  connectionLabel: document.querySelector("#connection-status .status-label"),
  serverSummary: document.getElementById("server-summary"),
};

const state = {
  activeRootId: "overview",
  selectedNodeId: "overview",
  selectedTab: "summary",
  expandedNodeIds: new Set(),
  nodesById: {},
  rootNodeIds: [],
  resourceCache: {},
  sessionsIndex: {},
  eventSource: null,
  connectionState: "disconnected",
};

function createNode(nodeData) {
  const existing = state.nodesById[nodeData.id] || {};
  const merged = {
    hasChildren: false,
    childrenLoaded: false,
    childIds: [],
    status: "",
    badge: "",
    badges: [],
    stale: false,
    error: null,
    meta: "",
    path: null,
    entryType: null,
    loading: false,
    ...existing,
    ...nodeData,
  };
  if (!Array.isArray(merged.childIds)) {
    merged.childIds = [];
  }
  state.nodesById[merged.id] = merged;
  return merged;
}

function getNode(nodeId) {
  return state.nodesById[nodeId] || null;
}

function getCacheEntry(key) {
  return state.resourceCache[key] || {
    key,
    status: "idle",
    data: null,
    loadedAt: 0,
    stale: false,
    error: null,
  };
}

function setCacheEntry(key, patch) {
  const entry = {
    ...getCacheEntry(key),
    ...patch,
  };
  state.resourceCache[key] = entry;
  return entry;
}

function cacheKeyForNode(node) {
  switch (node.kind) {
    case "overview":
    case "sessions-root":
    case "turns-root":
    case "event-bus":
    case "config":
      return node.id;
    case "session":
    case "session-detail":
      return `session-detail:${node.sessionId}`;
    case "context":
      return `context:${node.sessionId}`;
    case "runtime":
      return `runtime:${node.sessionId}`;
    case "agent":
      return `agent:${node.sessionId}`;
    case "memory":
      return `memory:${node.sessionId}`;
    case "memory-document":
      return `memory-document:${node.sessionId}`;
    case "memory-entry-list":
      return `memory-entry-list:${node.sessionId}`;
    case "memory-entry":
      return `memory-entry:${node.sessionId}:${node.entryId || ""}`;
    case "memory-daily-list":
      return `memory-daily-list:${node.sessionId}`;
    case "memory-daily-file":
      return `memory-daily-file:${node.sessionId}:${node.date || ""}`;
    case "memory-settings":
      return `memory-settings:${node.sessionId}`;
    case "memory-audit":
      return `memory-audit:${node.sessionId}`;
    case "skills":
      return `skills:${node.sessionId}`;
    case "tools":
      return `tools:${node.sessionId}`;
    case "mcp":
      return `mcp:${node.sessionId}`;
    case "subagents":
      return `subagents:${node.sessionId}`;
    case "turns":
      return `turns:${node.sessionId}`;
    case "turn":
      return `turn:${node.turnId}`;
    case "logs":
      return `logs:${node.sessionId}`;
    case "log-session":
      return `log-session:${node.sessionId}:${node.path || "."}`;
    case "log-file":
      return node.entryType === "directory"
        ? `log-dir:${node.sessionId}:${node.path || "."}`
        : `log-file:${node.sessionId}:${node.path || "."}`;
    default:
      return node.id;
  }
}

function escapeHtml(value) {
  return String(value ?? "")
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#39;");
}

function previewText(value, limit = 72) {
  const text = String(value ?? "");
  return text.length > limit ? `${text.slice(0, limit - 1)}…` : text;
}

function shortId(value) {
  const text = String(value ?? "");
  return text.length <= 12 ? text : `${text.slice(0, 12)}…`;
}

function phaseTone(value) {
  const phase = String(value || "").toLowerCase();
  if (["active", "running", "completed", "connected", "ready", "loaded", "idle"].includes(phase)) {
    return "phase-active";
  }
  if (["busy", "queued", "stale", "warning"].includes(phase)) {
    return "phase-busy";
  }
  if (["closed", "failed", "error", "disconnected", "notloaded", "not-loaded"].includes(phase)) {
    return "phase-error";
  }
  return "";
}

function badgeHtml(badge) {
  const value = typeof badge === "string" ? {text: badge, tone: ""} : badge;
  const classes = ["badge"];
  if (value.tone) {
    classes.push(value.tone);
  }
  return `<span class="${classes.join(" ")}">${escapeHtml(value.text)}</span>`;
}

function resourceVersionForPayload(payload) {
  return payload?.metadata?.resourceVersion || `${Date.now()}`;
}

function unwrapSessionScopedResource(payload) {
  if (!payload) {
    return null;
  }
  if (payload.kind && String(payload.kind).endsWith("List")) {
    return Array.isArray(payload.items) && payload.items.length > 0 ? payload.items[0] : null;
  }
  return payload;
}

function extractSkillCatalog(payload) {
  const item = unwrapSessionScopedResource(payload);
  const status = item?.status || {};
  const activeSkills = Array.isArray(status.active_skills) ? [...status.active_skills].map(String) : [];
  const activeSkillSet = new Set(activeSkills);
  const warnings = Array.isArray(status.warnings) ? [...status.warnings].map(String) : [];
  const skills = (Array.isArray(status.skills) ? [...status.skills] : [])
    .map((skill) => ({
      name: String(skill?.name || ""),
      source: String(skill?.source || "unknown"),
      catalog_visible: Boolean(skill?.catalog_visible),
      body_line_count: Number(skill?.body_line_count || 0),
      short_description: String(skill?.short_description || ""),
    }))
    .sort((left, right) => {
      const leftActive = activeSkillSet.has(left.name) ? 0 : 1;
      const rightActive = activeSkillSet.has(right.name) ? 0 : 1;
      return (
        leftActive - rightActive
        || left.source.localeCompare(right.source)
        || left.name.localeCompare(right.name)
      );
    });

  return {
    sessionId: String(item?.spec?.session_id || item?.metadata?.name || ""),
    phase: String(status.phase || "Unknown"),
    activeSkills,
    warnings,
    skills,
  };
}

function normalizeObjectSchema(schema) {
  const normalized = schema && typeof schema === "object" ? {...schema} : {};
  const properties = normalized.properties && typeof normalized.properties === "object"
    ? {...normalized.properties}
    : {};
  normalized.type = typeof normalized.type === "string" ? normalized.type : "object";
  normalized.properties = properties;
  return normalized;
}

function schemaPropertyEntries(parametersSchema) {
  const schema = normalizeObjectSchema(parametersSchema);
  return Object.entries(schema.properties).map(([name, definition]) => ([
    String(name),
    definition && typeof definition === "object" ? definition : {},
  ]));
}

function parameterTypeLabel(definition) {
  if (Array.isArray(definition?.type) && definition.type.length > 0) {
    return definition.type.map((value) => String(value)).join(" | ");
  }
  if (typeof definition?.type === "string" && definition.type) {
    return definition.type;
  }
  if (Array.isArray(definition?.anyOf) && definition.anyOf.length > 0) {
    return definition.anyOf.map((item) => parameterTypeLabel(item)).join(" | ");
  }
  if (Array.isArray(definition?.oneOf) && definition.oneOf.length > 0) {
    return definition.oneOf.map((item) => parameterTypeLabel(item)).join(" | ");
  }
  if (Array.isArray(definition?.enum) && definition.enum.length > 0) {
    return "enum";
  }
  return "string";
}

function previewParameterNames(tool, limit = 4) {
  const required = new Set((tool?.requiredParameters || []).map((value) => String(value)));
  const labels = schemaPropertyEntries(tool?.parametersSchema)
    .map(([name]) => (required.has(name) ? `${name}*` : name));
  if (labels.length === 0) {
    return "No parameters";
  }
  if (labels.length <= limit) {
    return labels.join(", ");
  }
  return `${labels.slice(0, limit).join(", ")}, +${labels.length - limit} more`;
}

function extractToolCatalog(payload) {
  const item = unwrapSessionScopedResource(payload);
  const status = item?.status || {};
  const tools = (Array.isArray(status.tools) ? [...status.tools] : [])
    .map((tool) => {
      const fullName = String(tool?.name || "");
      const source = String(tool?.source || "unknown");
      const hasPrefix = source === "mcp" && fullName.includes(":");
      const server = hasPrefix ? fullName.split(":", 1)[0] : null;
      const displayName = String(tool?.display_name || (hasPrefix ? fullName.slice(server.length + 1) : fullName));
      const description = String(tool?.description || "");
      const parametersSchema = normalizeObjectSchema(tool?.parameters_schema);
      const requiredParameters = Array.isArray(tool?.required_parameters)
        ? [...tool.required_parameters].map((value) => String(value))
        : (Array.isArray(parametersSchema.required) ? [...parametersSchema.required].map((value) => String(value)) : []);
      const parameterCount = Number.isFinite(Number(tool?.parameter_count))
        ? Number(tool.parameter_count)
        : schemaPropertyEntries(parametersSchema).length;
      const functionSchema = tool?.function_schema && typeof tool.function_schema === "object"
        ? {
            ...tool.function_schema,
            name: String(tool.function_schema.name || fullName),
            description: String(tool.function_schema.description || description),
            parameters: normalizeObjectSchema(tool.function_schema.parameters || parametersSchema),
          }
        : {
            name: fullName,
            description,
            parameters: parametersSchema,
          };
      return {
        name: fullName,
        source,
        server,
        displayName,
        group: source === "mcp" && server ? `mcp:${server}` : "builtin",
        description,
        parametersSchema,
        requiredParameters,
        parameterCount,
        functionSchema,
      };
    })
    .sort((left, right) => {
      const leftRank = left.source === "builtin" ? 0 : 1;
      const rightRank = right.source === "builtin" ? 0 : 1;
      return (
        leftRank - rightRank
        || String(left.server || "").localeCompare(String(right.server || ""))
        || left.displayName.localeCompare(right.displayName)
      );
    });

  return {
    sessionId: String(item?.spec?.session_id || item?.metadata?.name || ""),
    phase: String(status.phase || "Unknown"),
    tools,
  };
}

function buildSkillItemPayload(sessionId, skill, activeSkills) {
  const activeSkillSet = new Set((activeSkills || []).map(String));
  const active = activeSkillSet.has(String(skill?.name || ""));
  return {
    apiVersion: "nano-claw/v1",
    kind: "SkillItemView",
    metadata: {
      name: String(skill?.name || ""),
      resourceVersion: `${Date.now()}`,
    },
    spec: {
      session_id: sessionId,
      source: String(skill?.source || "unknown"),
      catalog_visible: Boolean(skill?.catalog_visible),
      body_line_count: Number(skill?.body_line_count || 0),
      short_description: String(skill?.short_description || ""),
    },
    status: {
      phase: active ? "Active" : "Available",
      active,
    },
  };
}

function buildToolItemPayload(sessionId, tool) {
  return {
    apiVersion: "nano-claw/v1",
    kind: "ToolItemView",
    metadata: {
      name: String(tool?.name || ""),
      resourceVersion: `${Date.now()}`,
    },
    spec: {
      session_id: sessionId,
      source: String(tool?.source || "unknown"),
      group: String(tool?.group || "builtin"),
      server: tool?.server || null,
      tool_name: String(tool?.name || ""),
      display_name: String(tool?.displayName || tool?.name || ""),
      description: String(tool?.description || ""),
      parameter_count: Number(tool?.parameterCount || 0),
      required_parameters: Array.isArray(tool?.requiredParameters) ? [...tool.requiredParameters] : [],
      parameters_schema: normalizeObjectSchema(tool?.parametersSchema),
      function_schema: tool?.functionSchema && typeof tool.functionSchema === "object"
        ? {
            ...tool.functionSchema,
            parameters: normalizeObjectSchema(tool.functionSchema.parameters),
          }
        : {
            name: String(tool?.name || ""),
            description: String(tool?.description || ""),
            parameters: normalizeObjectSchema(tool?.parametersSchema),
          },
    },
    status: {
      phase: "Ready",
    },
  };
}

function buildContextPayload(sessionPayload, agentPayload) {
  const sessionId = sessionPayload?.metadata?.name || agentPayload?.metadata?.name || "unknown";
  const messages = Array.isArray(sessionPayload?.spec?.messages) ? sessionPayload.spec.messages : [];
  const summaryText = sessionPayload?.spec?.summary_text || null;
  const agentStatus = agentPayload?.status || {};
  const agentSpec = agentPayload?.spec || {};
  return {
    apiVersion: "nano-claw/v1",
    kind: "ContextView",
    metadata: {
      name: sessionId,
      resourceVersion: `${resourceVersionForPayload(sessionPayload)}:${resourceVersionForPayload(agentPayload)}`,
    },
    spec: {
      session_id: sessionId,
      cwd: agentSpec.cwd || null,
      session_mode: agentSpec.session_mode || null,
      summary_text: summaryText,
      transcript_preview: messages.slice(-8),
    },
    status: {
      phase: agentStatus.phase || sessionPayload?.status?.phase || "Unknown",
      busy: Boolean(agentStatus.busy),
      summary_present: agentStatus.summary_present ?? Boolean(summaryText),
      persisted_message_count: messages.length,
      context_message_count: agentStatus.context_message_count ?? messages.length,
      active_turn_id: agentStatus.active_turn_id || null,
      recent_turn_count: Array.isArray(sessionPayload?.status?.recent_turns)
        ? sessionPayload.status.recent_turns.length
        : 0,
    },
  };
}

function initRoots() {
  state.rootNodeIds = ROOT_DEFS.map((definition) => definition.id);
  ROOT_DEFS.forEach((definition) => {
    createNode({
      ...definition,
      rootId: definition.id,
      meta: definition.kind,
    });
  });
}

function replaceChildren(parentId, childNodes) {
  const parent = getNode(parentId);
  if (!parent) {
    return;
  }
  childNodes.forEach((child) => createNode(child));
  parent.childIds = childNodes.map((child) => child.id);
  parent.childrenLoaded = true;
}

function collectDescendantIds(nodeId) {
  const node = getNode(nodeId);
  if (!node) {
    return [];
  }
  const ids = [nodeId];
  (node.childIds || []).forEach((childId) => {
    ids.push(...collectDescendantIds(childId));
  });
  return ids;
}

function removeNodeSubtree(nodeId) {
  const node = getNode(nodeId);
  if (!node) {
    return;
  }
  const ids = collectDescendantIds(nodeId);
  if (node.parentId) {
    const parent = getNode(node.parentId);
    if (parent) {
      parent.childIds = (parent.childIds || []).filter((childId) => childId !== nodeId);
    }
  }
  ids.forEach((id) => {
    delete state.nodesById[id];
  });
}

function evictMissingSession(sessionId) {
  removeNodeSubtree(`session:${sessionId}`);
  delete state.sessionsIndex[sessionId];

  Object.keys(state.resourceCache).forEach((key) => {
    if (key.includes(`:${sessionId}`) || key.endsWith(sessionId)) {
      delete state.resourceCache[key];
    }
  });

  const selectedNode = getNode(state.selectedNodeId);
  if (!selectedNode || selectedNode.sessionId === sessionId) {
    state.activeRootId = "sessions-root";
    state.selectedNodeId = "sessions-root";
  }
}

function runtimeSnapshotMap() {
  const entry = getCacheEntry("runtime-snapshot-list");
  const payload = entry.data;
  if (!payload || !Array.isArray(payload.items)) {
    return {};
  }
  const mapping = {};
  payload.items.forEach((item) => {
    mapping[item.metadata?.name] = item;
  });
  return mapping;
}

function updateNodeBadges(nodeId, badges) {
  const node = getNode(nodeId);
  if (!node) {
    return;
  }
  node.badges = badges;
  node.badge = badges.map((item) => (typeof item === "string" ? item : item.text)).join(" • ");
}

function sessionNodeBadges(sessionItem) {
  const sessionId = sessionItem.metadata?.name;
  const runtimeMap = runtimeSnapshotMap();
  const runtimeItem = runtimeMap[sessionId];
  const badges = [];
  badges.push({
    text: sessionItem.status?.state || sessionItem.status?.phase || "unknown",
    tone: phaseTone(sessionItem.status?.state || sessionItem.status?.phase),
  });
  if (runtimeItem) {
    badges.push({
      text: runtimeItem.status?.busy ? "busy" : "idle",
      tone: phaseTone(runtimeItem.status?.busy ? "busy" : "idle"),
    });
  } else {
    badges.push({text: "not-loaded", tone: phaseTone("not-loaded")});
  }
  badges.push({text: `${sessionItem.status?.turn_count || 0} turns`, tone: ""});
  return badges;
}

function materializeSessions(payload) {
  const items = Array.isArray(payload?.items) ? payload.items : [];
  const childNodes = items.map((item) => {
    const sessionId = item.metadata?.name;
    state.sessionsIndex[sessionId] = item;
    return {
      id: `session:${sessionId}`,
      kind: "session",
      label: item.spec?.title || sessionId,
      parentId: "sessions-root",
      rootId: "sessions-root",
      sessionId,
      hasChildren: true,
      childrenLoaded: false,
      meta: sessionId,
      status: item.status?.state || item.status?.phase || "",
      badges: sessionNodeBadges(item),
      stale: false,
      error: null,
    };
  });
  replaceChildren("sessions-root", childNodes);
  const sessionsRoot = getNode("sessions-root");
  if (sessionsRoot) {
    updateNodeBadges("sessions-root", [{text: `${items.length} sessions`, tone: ""}]);
  }
}

function materializeSessionChildren(sessionNodeId) {
  const sessionNode = getNode(sessionNodeId);
  if (!sessionNode || sessionNode.childrenLoaded) {
    return;
  }
  const childNodes = SESSION_CHILD_NODE_DEFS.map((definition) => ({
    id: `${sessionNodeId}:${definition.kind}`,
    kind: definition.kind,
    label: definition.label,
    parentId: sessionNodeId,
    rootId: sessionNode.rootId,
    sessionId: sessionNode.sessionId,
    hasChildren: definition.hasChildren,
    childrenLoaded: false,
    meta: sessionNode.sessionId,
    stale: false,
    error: null,
  }));
  replaceChildren(sessionNodeId, childNodes);
}

function materializeTurns(parentNodeId, payload, sessionId = null) {
  const items = Array.isArray(payload?.items) ? payload.items : [];
  const parentNode = getNode(parentNodeId);
  if (!parentNode) {
    return;
  }
  const childNodes = items.map((item) => {
    const turnId = item.metadata?.name;
    const prefix = sessionId ? `${parentNodeId}:turn:` : "turn:";
    return {
      id: `${prefix}${turnId}`,
      kind: "turn",
      label: `${shortId(turnId)} ${previewText(item.spec?.input_text || "", 40)}`.trim(),
      parentId: parentNodeId,
      rootId: parentNode.rootId,
      sessionId: item.spec?.session_id || sessionId,
      turnId,
      hasChildren: false,
      childrenLoaded: false,
      meta: item.spec?.created_at || "",
      status: item.status?.status || item.status?.phase || "",
      badges: [
        {
          text: item.status?.status || item.status?.phase || "unknown",
          tone: phaseTone(item.status?.status || item.status?.phase),
        },
      ],
      stale: false,
      error: null,
    };
  });
  replaceChildren(parentNodeId, childNodes);
  updateNodeBadges(parentNodeId, [{text: `${items.length} shown`, tone: ""}]);
}

function materializeLogSessions(parentNodeId, payload) {
  const items = Array.isArray(payload?.items) ? payload.items : [];
  const parentNode = getNode(parentNodeId);
  if (!parentNode) {
    return;
  }
  const childNodes = items.map((item, index) => ({
    id: `${parentNodeId}:log-session:${index}`,
    kind: "log-session",
    label: item.spec?.session_dir ? previewText(item.spec.session_dir.split("/").slice(-1)[0], 48) : "session-dir",
    parentId: parentNodeId,
    rootId: parentNode.rootId,
    sessionId: item.spec?.session_id || parentNode.sessionId,
    path: ".",
    hasChildren: true,
    childrenLoaded: false,
    meta: item.spec?.session_dir || "",
    status: item.status?.phase || "",
    badges: [
      {
        text: item.status?.phase || "unknown",
        tone: phaseTone(item.status?.phase),
      },
      {
        text: `${item.status?.llm_call_count || 0} llm`,
        tone: "",
      },
    ],
    stale: false,
    error: null,
  }));
  replaceChildren(parentNodeId, childNodes);
  updateNodeBadges(parentNodeId, [{text: `${items.length} logs`, tone: ""}]);
}

function materializeLogFiles(parentNodeId, payload) {
  const items = Array.isArray(payload?.items) ? payload.items : [];
  const parentNode = getNode(parentNodeId);
  if (!parentNode) {
    return;
  }
  const childNodes = items.map((item) => {
    const relativePath = item.spec?.relative_path || ".";
    const label = relativePath === "." ? "." : relativePath.split("/").slice(-1)[0];
    return {
      id: `${parentNodeId}:file:${relativePath}`,
      kind: "log-file",
      label,
      parentId: parentNodeId,
      rootId: parentNode.rootId,
      sessionId: item.spec?.session_id || parentNode.sessionId,
      path: relativePath,
      entryType: item.spec?.type || "file",
      hasChildren: item.spec?.type === "directory",
      childrenLoaded: false,
      meta: relativePath,
      status: item.spec?.type || "",
      badges: [
        {
          text: item.spec?.type || "file",
          tone: phaseTone(item.spec?.type === "directory" ? "running" : ""),
        },
        {text: `${item.status?.bytes || 0} bytes`, tone: ""},
      ],
      stale: false,
      error: null,
    };
  });
  replaceChildren(parentNodeId, childNodes);
  updateNodeBadges(parentNodeId, [{text: `${items.length} entries`, tone: ""}]);
}

function materializeSkillItems(parentNodeId, payload) {
  const parentNode = getNode(parentNodeId);
  if (!parentNode) {
    return;
  }
  const catalog = extractSkillCatalog(payload);
  const activeSkillSet = new Set(catalog.activeSkills);
  const childNodes = catalog.skills.map((skill) => ({
    id: `${parentNodeId}:skill:${skill.name}`,
    kind: "skill-item",
    label: skill.name,
    parentId: parentNodeId,
    rootId: parentNode.rootId,
    sessionId: catalog.sessionId || parentNode.sessionId,
    hasChildren: false,
    childrenLoaded: false,
    skillName: skill.name,
    meta: skill.short_description || skill.source,
    status: activeSkillSet.has(skill.name) ? "active" : "available",
    badges: [
      ...(activeSkillSet.has(skill.name) ? [{text: "active", tone: "phase-active"}] : []),
      {text: skill.source, tone: ""},
      ...(skill.body_line_count > 0 ? [{text: `${skill.body_line_count} lines`, tone: ""}] : []),
    ],
    stale: false,
    error: null,
  }));
  replaceChildren(parentNodeId, childNodes);
}

function materializeMemoryChildren(parentNodeId, payload) {
  const parentNode = getNode(parentNodeId);
  if (!parentNode) {
    return;
  }
  if (payload?.status?.phase === "Disabled") {
    replaceChildren(parentNodeId, []);
    return;
  }
  replaceChildren(parentNodeId, [
    {
      id: `${parentNodeId}:document`,
      kind: "memory-document",
      label: "MEMORY.md",
      parentId: parentNodeId,
      rootId: parentNode.rootId,
      sessionId: parentNode.sessionId,
      hasChildren: false,
      childrenLoaded: false,
      meta: "curated memory",
      stale: false,
      error: null,
    },
    {
      id: `${parentNodeId}:entries`,
      kind: "memory-entry-list",
      label: "Curated Entries",
      parentId: parentNodeId,
      rootId: parentNode.rootId,
      sessionId: parentNode.sessionId,
      hasChildren: true,
      childrenLoaded: false,
      meta: `${payload?.status?.entry_count || 0} entries`,
      stale: false,
      error: null,
    },
    {
      id: `${parentNodeId}:daily`,
      kind: "memory-daily-list",
      label: "Daily Logs",
      parentId: parentNodeId,
      rootId: parentNode.rootId,
      sessionId: parentNode.sessionId,
      hasChildren: true,
      childrenLoaded: false,
      meta: "append-only notes",
      stale: false,
      error: null,
    },
    {
      id: `${parentNodeId}:settings`,
      kind: "memory-settings",
      label: "Settings",
      parentId: parentNodeId,
      rootId: parentNode.rootId,
      sessionId: parentNode.sessionId,
      hasChildren: false,
      childrenLoaded: false,
      meta: payload?.status?.mode || "",
      stale: false,
      error: null,
    },
    {
      id: `${parentNodeId}:audit`,
      kind: "memory-audit",
      label: "Audit",
      parentId: parentNodeId,
      rootId: parentNode.rootId,
      sessionId: parentNode.sessionId,
      hasChildren: false,
      childrenLoaded: false,
      meta: "writes, retrievals, injections",
      stale: false,
      error: null,
    },
  ]);
}

function materializeMemoryEntries(parentNodeId, payload) {
  const parentNode = getNode(parentNodeId);
  if (!parentNode) {
    return;
  }
  const items = Array.isArray(payload?.items) ? payload.items : [];
  const childNodes = items.map((item) => ({
    id: `${parentNodeId}:entry:${item?.metadata?.name || ""}`,
    kind: "memory-entry",
    label: item?.spec?.title || item?.metadata?.name || "entry",
    parentId: parentNodeId,
    rootId: parentNode.rootId,
    sessionId: parentNode.sessionId,
    entryId: item?.metadata?.name || "",
    hasChildren: false,
    childrenLoaded: false,
    meta: item?.spec?.kind || "",
    status: item?.status?.status || "",
    badges: [
      ...(item?.spec?.kind ? [{text: item.spec.kind, tone: ""}] : []),
      ...(item?.status?.status ? [{text: item.status.status, tone: phaseTone(item.status.status)}] : []),
    ],
    stale: false,
    error: null,
  }));
  replaceChildren(parentNodeId, childNodes);
  updateNodeBadges(parentNodeId, [{text: `${items.length} entries`, tone: ""}]);
}

function materializeMemoryDailyFiles(parentNodeId, payload) {
  const parentNode = getNode(parentNodeId);
  if (!parentNode) {
    return;
  }
  const items = Array.isArray(payload?.items) ? payload.items : [];
  const childNodes = items.map((item) => {
    const date = String(item?.spec?.date || item?.metadata?.date || "");
    return {
      id: `${parentNodeId}:daily:${date}`,
      kind: "memory-daily-file",
      label: date,
      parentId: parentNodeId,
      rootId: parentNode.rootId,
      sessionId: parentNode.sessionId,
      date,
      hasChildren: false,
      childrenLoaded: false,
      meta: item?.spec?.path || "",
      stale: false,
      error: null,
    };
  });
  replaceChildren(parentNodeId, childNodes);
  updateNodeBadges(parentNodeId, [{text: `${items.length} logs`, tone: ""}]);
}

function materializeToolItems(parentNodeId, payload) {
  const parentNode = getNode(parentNodeId);
  if (!parentNode) {
    return;
  }
  const catalog = extractToolCatalog(payload);
  const childNodes = catalog.tools.map((tool) => ({
    id: `${parentNodeId}:tool:${tool.name}`,
    kind: "tool-item",
    label: tool.displayName,
    parentId: parentNodeId,
    rootId: parentNode.rootId,
    sessionId: catalog.sessionId || parentNode.sessionId,
    hasChildren: false,
    childrenLoaded: false,
    toolName: tool.name,
    meta: tool.description || (tool.source === "mcp" && tool.server ? `mcp:${tool.server}` : tool.source),
    status: tool.source,
    badges: [
      {text: tool.source, tone: ""},
      ...(tool.server ? [{text: tool.server, tone: ""}] : []),
      {text: `${tool.parameterCount} params`, tone: ""},
    ],
    stale: false,
    error: null,
  }));
  replaceChildren(parentNodeId, childNodes);
}

function applyPayloadToNode(nodeId, payload) {
  const node = getNode(nodeId);
  if (!node) {
    return;
  }

  if (node.kind === "overview") {
    node.status = payload.status?.phase || "Ready";
    updateNodeBadges(nodeId, [
      {text: `${payload.status?.session_count || 0} sessions`, tone: ""},
      {text: `${payload.status?.runtime_count || 0} runtimes`, tone: ""},
    ]);
    renderServerSummary(payload);
    return;
  }

  if (node.kind === "sessions-root") {
    materializeSessions(payload);
    return;
  }

  if (node.kind === "turns-root") {
    node.status = payload.kind || "TurnList";
    materializeTurns(nodeId, payload, null);
    return;
  }

  if (node.kind === "event-bus") {
    node.status = payload.status?.phase || "Running";
    updateNodeBadges(nodeId, [
      {text: `${payload.status?.closed_turn_count || 0} closed`, tone: ""},
    ]);
    return;
  }

  if (node.kind === "config") {
    node.status = payload.status?.phase || "Ready";
    updateNodeBadges(nodeId, [
      {text: "sanitized", tone: ""},
    ]);
    return;
  }

  if (node.kind === "session" || node.kind === "session-detail") {
    node.label = payload.spec?.title || node.label;
    node.status = payload.status?.state || payload.status?.phase || "";
    node.meta = node.sessionId;
    updateNodeBadges(node.id, sessionNodeBadges(state.sessionsIndex[node.sessionId] || payload));
    return;
  }

  if (node.kind === "context") {
    node.status = payload.status?.phase || "";
    updateNodeBadges(node.id, [
      {
        text: payload.status?.summary_present ? "summary" : "raw-history",
        tone: payload.status?.summary_present ? "phase-active" : "",
      },
      {
        text: `${payload.status?.context_message_count || 0} msgs`,
        tone: "",
      },
    ]);
    return;
  }

  if (node.kind === "runtime" || node.kind === "agent") {
    node.status = payload.status?.phase || "";
    updateNodeBadges(node.id, [
      {
        text: payload.status?.busy ? "busy" : "idle",
        tone: phaseTone(payload.status?.busy ? "busy" : "idle"),
      },
      {
        text: payload.status?.active_turn_id ? shortId(payload.status.active_turn_id) : "no active turn",
        tone: "",
      },
    ]);
    return;
  }

  if (node.kind === "memory") {
    node.status = payload.status?.phase || "";
    updateNodeBadges(node.id, [
      ...(payload.status?.phase === "Disabled" ? [{text: "disabled", tone: "phase-error"}] : []),
      ...(payload.status?.phase !== "Disabled" ? [{text: `${payload.status?.entry_count || 0} entries`, tone: ""}] : []),
      ...(payload.status?.phase !== "Disabled" ? [{text: `${payload.status?.daily_file_count || 0} daily`, tone: ""}] : []),
      ...(payload.status?.mode ? [{text: payload.status.mode, tone: ""}] : []),
    ]);
    if (state.expandedNodeIds.has(node.id)) {
      materializeMemoryChildren(node.id, payload);
    }
    return;
  }

  if (node.kind === "memory-document") {
    node.status = payload.status?.phase || "";
    updateNodeBadges(node.id, [
      {text: `${String(payload.status?.content || "").length} chars`, tone: ""},
    ]);
    return;
  }

  if (node.kind === "memory-entry-list") {
    node.status = payload.kind || "SessionMemoryEntryList";
    materializeMemoryEntries(node.id, payload);
    return;
  }

  if (node.kind === "memory-entry") {
    node.status = payload.status?.status || payload.status?.phase || "";
    updateNodeBadges(node.id, [
      ...(payload.spec?.kind ? [{text: payload.spec.kind, tone: ""}] : []),
      ...(payload.status?.status ? [{text: payload.status.status, tone: phaseTone(payload.status.status)}] : []),
    ]);
    return;
  }

  if (node.kind === "memory-daily-list") {
    node.status = payload.kind || "SessionMemoryDailyLogList";
    materializeMemoryDailyFiles(node.id, payload);
    return;
  }

  if (node.kind === "memory-daily-file") {
    node.status = payload.status?.phase || "";
    updateNodeBadges(node.id, [
      {text: `${String(payload.status?.content || "").length} chars`, tone: ""},
    ]);
    return;
  }

  if (node.kind === "memory-settings") {
    node.status = payload.status?.phase || "";
    updateNodeBadges(node.id, [
      ...(payload.status?.mode ? [{text: payload.status.mode, tone: ""}] : []),
    ]);
    return;
  }

  if (node.kind === "memory-audit") {
    const count = Array.isArray(payload?.items) ? payload.items.length : 0;
    node.status = payload.kind || "SessionMemoryAuditEventList";
    updateNodeBadges(node.id, [{text: `${count} events`, tone: ""}]);
    return;
  }

  if (node.kind === "skills") {
    const catalog = extractSkillCatalog(payload);
    node.status = catalog.phase;
    updateNodeBadges(node.id, [
      {text: `${catalog.skills.length} skills`, tone: ""},
      ...(catalog.activeSkills.length > 0 ? [{text: `${catalog.activeSkills.length} active`, tone: "phase-active"}] : []),
      ...(catalog.warnings.length > 0 ? [{text: `${catalog.warnings.length} warnings`, tone: "phase-busy"}] : []),
    ]);
    if (state.expandedNodeIds.has(node.id)) {
      materializeSkillItems(node.id, payload);
    }
    return;
  }

  if (node.kind === "tools") {
    const catalog = extractToolCatalog(payload);
    const builtinCount = catalog.tools.filter((tool) => tool.source === "builtin").length;
    const mcpCount = catalog.tools.length - builtinCount;
    node.status = catalog.phase;
    updateNodeBadges(node.id, [
      {text: `${catalog.tools.length} tools`, tone: ""},
      {text: `${builtinCount} builtin`, tone: ""},
      ...(mcpCount > 0 ? [{text: `${mcpCount} mcp`, tone: ""}] : []),
    ]);
    if (state.expandedNodeIds.has(node.id)) {
      materializeToolItems(node.id, payload);
    }
    return;
  }

  if (["mcp", "subagents", "logs"].includes(node.kind)) {
    const count = Array.isArray(payload?.items) ? payload.items.length : 0;
    node.status = payload.kind || node.kind;
    const countLabel = node.kind === "mcp" ? `${count} runtime(s)` : `${count} item(s)`;
    updateNodeBadges(node.id, [{text: countLabel, tone: ""}]);
    if (node.kind === "logs") {
      materializeLogSessions(node.id, payload);
    }
    return;
  }

  if (node.kind === "skill-item") {
    node.status = payload.status?.phase || "";
    return;
  }

  if (node.kind === "tool-item") {
    node.status = payload.status?.phase || "";
    return;
  }

  if (node.kind === "turns") {
    node.status = payload.kind || "TurnList";
    materializeTurns(node.id, payload, node.sessionId);
    return;
  }

  if (node.kind === "turn") {
    node.status = payload.status?.status || payload.status?.phase || "";
    updateNodeBadges(node.id, [
      {
        text: payload.status?.status || payload.status?.phase || "unknown",
        tone: phaseTone(payload.status?.status || payload.status?.phase),
      },
    ]);
    return;
  }

  if (node.kind === "log-session") {
    node.status = payload.kind || "LogFileList";
    materializeLogFiles(node.id, payload);
    return;
  }

  if (node.kind === "log-file") {
    if (node.entryType === "directory") {
      materializeLogFiles(node.id, payload);
    } else {
      node.status = payload.status?.phase || "";
      updateNodeBadges(node.id, [
        {text: `${payload.status?.line_count || 0} lines`, tone: ""},
      ]);
    }
  }
}

async function fetchJson(url) {
  const response = await fetch(url);
  if (!response.ok) {
    const payload = await response.json().catch(() => ({}));
    const error = new Error(payload.detail || response.statusText);
    error.status = response.status;
    throw error;
  }
  return response.json();
}

function makeUrl(path, params = {}) {
  const query = new URLSearchParams();
  Object.entries(params).forEach(([key, value]) => {
    if (value !== undefined && value !== null && value !== "") {
      query.set(key, String(value));
    }
  });
  const suffix = query.toString();
  return suffix ? `${path}?${suffix}` : path;
}

async function fetchNodePayload(node) {
  switch (node.kind) {
    case "overview":
      return fetchJson("/api/v1/admin/overview");
    case "sessions-root":
      return fetchJson("/api/v1/admin/sessions");
    case "turns-root":
      return fetchJson(makeUrl("/api/v1/admin/turns", {limit: 100}));
    case "event-bus":
      return fetchJson("/api/v1/admin/event-bus");
    case "config":
      return fetchJson("/api/v1/admin/config");
    case "session":
    case "session-detail":
      return fetchJson(`/api/v1/admin/sessions/${encodeURIComponent(node.sessionId)}`);
    case "runtime":
      return fetchJson(`/api/v1/admin/runtimes/${encodeURIComponent(node.sessionId)}`);
    case "agent":
      return fetchJson(`/api/v1/admin/agent-runtimes/${encodeURIComponent(node.sessionId)}`);
    case "memory":
      return fetchJson(makeUrl("/api/v1/admin/memory", {session_id: node.sessionId}));
    case "memory-document":
      return fetchJson(makeUrl("/api/v1/admin/memory/document", {session_id: node.sessionId}));
    case "memory-entry-list":
      return fetchJson(makeUrl("/api/v1/admin/memory/entries", {session_id: node.sessionId}));
    case "memory-entry":
      return fetchJson(
        makeUrl(`/api/v1/admin/memory/entries/${encodeURIComponent(node.entryId || "")}`, {
          session_id: node.sessionId,
        }),
      );
    case "memory-daily-list":
      return fetchJson(makeUrl("/api/v1/admin/memory/daily", {session_id: node.sessionId}));
    case "memory-daily-file":
      return fetchJson(
        makeUrl(`/api/v1/admin/memory/daily/${encodeURIComponent(node.date || "")}`, {
          session_id: node.sessionId,
        }),
      );
    case "memory-settings":
      return fetchJson(makeUrl("/api/v1/admin/memory/settings", {session_id: node.sessionId}));
    case "memory-audit":
      return fetchJson(makeUrl("/api/v1/admin/memory/audit", {session_id: node.sessionId, limit: 100}));
    case "context": {
      const [sessionPayload, agentPayload] = await Promise.all([
        fetchJson(`/api/v1/admin/sessions/${encodeURIComponent(node.sessionId)}`),
        fetchJson(`/api/v1/admin/agent-runtimes/${encodeURIComponent(node.sessionId)}`),
      ]);
      return buildContextPayload(sessionPayload, agentPayload);
    }
    case "skills":
      return fetchJson(makeUrl("/api/v1/admin/skills", {session_id: node.sessionId}));
    case "tools":
      return fetchJson(makeUrl("/api/v1/admin/tools", {session_id: node.sessionId}));
    case "skill-item": {
      const parent = getNode(node.parentId);
      if (!parent) {
        throw new Error(`Missing parent node for ${node.id}`);
      }
      const parentPayload = await loadNodeData(parent.id);
      const catalog = extractSkillCatalog(parentPayload);
      const skill = catalog.skills.find((item) => item.name === node.skillName || item.name === node.label);
      if (!skill) {
        throw new Error(`Unknown skill: ${node.label}`);
      }
      return buildSkillItemPayload(catalog.sessionId || node.sessionId, skill, catalog.activeSkills);
    }
    case "tool-item": {
      const parent = getNode(node.parentId);
      if (!parent) {
        throw new Error(`Missing parent node for ${node.id}`);
      }
      const parentPayload = await loadNodeData(parent.id);
      const catalog = extractToolCatalog(parentPayload);
      const tool = catalog.tools.find((item) => item.name === node.toolName || item.displayName === node.label);
      if (!tool) {
        throw new Error(`Unknown tool: ${node.label}`);
      }
      return buildToolItemPayload(catalog.sessionId || node.sessionId, tool);
    }
    case "mcp":
      return fetchJson(makeUrl("/api/v1/admin/mcp", {session_id: node.sessionId}));
    case "subagents":
      return fetchJson(makeUrl("/api/v1/admin/subagents", {session_id: node.sessionId}));
    case "turns":
      return fetchJson(makeUrl("/api/v1/admin/turns", {session_id: node.sessionId, limit: 100}));
    case "turn":
      return fetchJson(`/api/v1/admin/turns/${encodeURIComponent(node.turnId)}`);
    case "logs":
      return fetchJson(makeUrl("/api/v1/admin/log-sessions", {session_id: node.sessionId}));
    case "log-session":
      return fetchJson(makeUrl("/api/v1/admin/log-files", {session_id: node.sessionId, path: node.path || "."}));
    case "log-file":
      if (node.entryType === "directory") {
        return fetchJson(makeUrl("/api/v1/admin/log-files", {session_id: node.sessionId, path: node.path || "."}));
      }
      return fetchJson(
        makeUrl("/api/v1/admin/log-files/tail", {
          session_id: node.sessionId,
          file: node.path || ".",
          lines: 200,
          redacted: true,
        }),
      );
    default:
      throw new Error(`Unsupported node kind: ${node.kind}`);
  }
}

async function loadNodeData(nodeId, {force = false} = {}) {
  const node = getNode(nodeId);
  if (!node) {
    return null;
  }
  const cacheKey = cacheKeyForNode(node);
  const existing = getCacheEntry(cacheKey);
  if (!force && existing.status === "ready" && !existing.stale) {
    return existing.data;
  }

  node.loading = true;
  node.error = null;
  node.stale = false;
  setCacheEntry(cacheKey, {status: "loading", stale: false, error: null});
  renderAll();

  try {
    const payload = await fetchNodePayload(node);
    setCacheEntry(cacheKey, {
      status: "ready",
      data: payload,
      loadedAt: Date.now(),
      stale: false,
      error: null,
    });
    node.loading = false;
    node.stale = false;
    applyPayloadToNode(nodeId, payload);
    if (node.kind === "sessions-root") {
      const runtimesPayload = getCacheEntry("runtime-snapshot-list").data;
      if (runtimesPayload) {
        applyRuntimeListSnapshot(runtimesPayload);
      }
    }
    renderAll();
    return payload;
  } catch (error) {
    const message = error instanceof Error ? error.message : String(error);
    if (error?.status === 404 && node.sessionId) {
      evictMissingSession(node.sessionId);
      renderAll();
      throw error;
    }
    setCacheEntry(cacheKey, {
      status: "error",
      error: message,
      stale: false,
    });
    node.loading = false;
    node.error = message;
    renderAll();
    throw error;
  }
}

async function ensureRootLoaded(rootId) {
  const root = getNode(rootId);
  if (!root) {
    return;
  }
  if (root.kind === "sessions-root") {
    await loadNodeData(rootId);
    return;
  }
  if (root.kind === "turns-root" || root.kind === "overview" || root.kind === "event-bus" || root.kind === "config") {
    await loadNodeData(rootId);
  }
}

async function ensureExpandedData(nodeId) {
  const node = getNode(nodeId);
  if (!node) {
    return;
  }
  if (node.kind === "session") {
    materializeSessionChildren(nodeId);
    return;
  }
  if (["sessions-root", "turns-root", "turns", "logs", "log-session", "skills", "tools", "memory", "memory-entry-list", "memory-daily-list"].includes(node.kind)) {
    await loadNodeData(nodeId);
    return;
  }
  if (node.kind === "log-file" && node.entryType === "directory") {
    await loadNodeData(nodeId);
  }
}

async function selectNode(nodeId, {force = false} = {}) {
  const node = getNode(nodeId);
  if (!node) {
    return;
  }
  state.selectedNodeId = nodeId;
  if (!ROOT_KINDS.has(node.kind)) {
    state.activeRootId = node.rootId || state.activeRootId;
  }
  if (!force) {
    try {
      await loadNodeData(nodeId);
    } catch (_error) {
      // Detail rendering uses the node error state.
    }
  } else {
    try {
      await loadNodeData(nodeId, {force: true});
    } catch (_error) {
      // Detail rendering uses the node error state.
    }
  }
  renderAll();
}

async function toggleNode(nodeId) {
  if (state.expandedNodeIds.has(nodeId)) {
    state.expandedNodeIds.delete(nodeId);
    renderTree();
    return;
  }
  state.expandedNodeIds.add(nodeId);
  renderTree();
  try {
    await ensureExpandedData(nodeId);
  } catch (_error) {
    // Expansion errors are displayed on the node itself.
  }
  renderAll();
}

function setConnectionState(mode) {
  state.connectionState = mode;
  elements.connectionStatus.className = `connection-status ${mode}`;
  elements.connectionLabel.textContent = mode;
}

function renderServerSummary(payload) {
  if (!payload) {
    elements.serverSummary.innerHTML = "";
    return;
  }
  const runtimeCount = payload.status?.runtime_count ?? 0;
  const busyRuntimeCount = payload.status?.busy_runtime_count ?? 0;
  const turnCounts = payload.status?.turn_status_counts || {};
  elements.serverSummary.innerHTML = [
    badgeHtml({text: `${payload.status?.session_count || 0} sessions`, tone: ""}),
    badgeHtml({text: `${runtimeCount} runtimes`, tone: ""}),
    badgeHtml({text: `${busyRuntimeCount} busy`, tone: busyRuntimeCount > 0 ? "phase-busy" : ""}),
    badgeHtml({text: `${turnCounts.running || 0} running turns`, tone: turnCounts.running ? "phase-busy" : ""}),
  ].join("");
}

function renderNav() {
  elements.adminNav.innerHTML = "";
  ROOT_DEFS.forEach((rootDef) => {
    const root = getNode(rootDef.id);
    const button = document.createElement("button");
    button.type = "button";
    button.className = state.activeRootId === rootDef.id ? "active" : "";
    const badges = Array.isArray(root?.badges) ? root.badges.map(badgeHtml).join("") : "";
    button.innerHTML = `
      <span class="nav-label">
        <span>${escapeHtml(rootDef.label)}</span>
        <span class="nav-meta">${badges}</span>
      </span>
    `;
    button.addEventListener("click", async () => {
      state.activeRootId = rootDef.id;
      if (getNode(rootDef.id)?.hasChildren) {
        state.expandedNodeIds.add(rootDef.id);
      }
      await ensureRootLoaded(rootDef.id);
      await selectNode(rootDef.id);
    });
    elements.adminNav.appendChild(button);
  });
}

function renderTreeNode(nodeId, depth = 0) {
  const node = getNode(nodeId);
  if (!node) {
    return "";
  }
  const expanded = state.expandedNodeIds.has(nodeId);
  const selected = state.selectedNodeId === nodeId;
  const badges = [...(node.badges || [])];
  if (node.loading) {
    badges.unshift({text: "loading", tone: "phase-busy"});
  }
  if (node.stale) {
    badges.unshift({text: "stale", tone: "phase-busy"});
  }
  if (node.error) {
    badges.unshift({text: "error", tone: "phase-error"});
  }

  const childrenMarkup =
    expanded && node.childIds.length > 0
      ? `<div class="tree-children">${node.childIds.map((childId) => renderTreeNode(childId, depth + 1)).join("")}</div>`
      : "";

  return `
    <div class="tree-group" data-node-group="${escapeHtml(nodeId)}">
      <div class="tree-row${selected ? " selected" : ""}" data-node-row="${escapeHtml(nodeId)}">
        <button
          class="tree-toggle${node.hasChildren ? "" : " placeholder"}"
          type="button"
          data-action="toggle"
          data-node-id="${escapeHtml(nodeId)}"
        >
          ${node.hasChildren ? escapeHtml(expanded ? "▾" : "▸") : "•"}
        </button>
        <div class="tree-main" data-action="select" data-node-id="${escapeHtml(nodeId)}">
          <div class="tree-title-line">
            <span class="tree-label">${escapeHtml(node.label)}</span>
          </div>
          <div class="tree-meta">${escapeHtml(node.meta || node.kind)}</div>
        </div>
        <div class="tree-badges">${badges.map(badgeHtml).join("")}</div>
      </div>
      ${childrenMarkup}
    </div>
  `;
}

function renderTree() {
  const root = getNode(state.activeRootId);
  if (!root) {
    elements.treeView.innerHTML = `<div class="detail-empty">No root selected.</div>`;
    return;
  }
  elements.treeView.innerHTML = renderTreeNode(root.id);
  elements.treeView.querySelectorAll("[data-action='toggle']").forEach((button) => {
    button.addEventListener("click", async (event) => {
      event.stopPropagation();
      await toggleNode(button.getAttribute("data-node-id"));
    });
  });
  elements.treeView.querySelectorAll("[data-action='select']").forEach((element) => {
    element.addEventListener("click", async () => {
      await selectNode(element.getAttribute("data-node-id"));
    });
  });
}

function currentPayload() {
  const node = getNode(state.selectedNodeId);
  if (!node) {
    return null;
  }
  return getCacheEntry(cacheKeyForNode(node)).data;
}

function renderObjectCards(data, title) {
  if (!data || typeof data !== "object") {
    return "";
  }
  const entries = Object.entries(data);
  if (entries.length === 0) {
    return "";
  }
  return `
    <section class="detail-section">
      <h3>${escapeHtml(title)}</h3>
      <div class="detail-grid">
        ${entries
          .map(
            ([key, value]) => `
              <div class="detail-card">
                <strong>${escapeHtml(key)}</strong>
                <code>${escapeHtml(typeof value === "string" ? value : JSON.stringify(value, null, 2))}</code>
              </div>
            `,
          )
          .join("")}
      </div>
    </section>
  `;
}

function renderListSummary(payload) {
  const items = Array.isArray(payload?.items) ? payload.items : [];
  const previewItems = items.slice(0, 20);
  return `
    <section class="detail-section">
      <h3>Collection</h3>
      <div class="summary-list">
        <div class="summary-item">
          <strong>${escapeHtml(payload.kind)}</strong>
          <div>${escapeHtml(`${items.length} item(s)`)}</div>
        </div>
        ${previewItems
          .map((item) => {
            const name = item.metadata?.name || item.kind || "item";
            const phase = item.status?.phase || item.status?.status || "";
            return `
              <div class="summary-item">
                <strong>${escapeHtml(name)}</strong>
                <div>${escapeHtml(phase)}</div>
              </div>
            `;
          })
          .join("")}
      </div>
    </section>
  `;
}

function renderBadgeList(values, emptyLabel) {
  if (!Array.isArray(values) || values.length === 0) {
    return `<div class="detail-empty-inline">${escapeHtml(emptyLabel)}</div>`;
  }
  return `
    <div class="inline-badges">
      ${values.map((value) => badgeHtml({text: value, tone: ""})).join("")}
    </div>
  `;
}

function renderKeyValueSummary(rows) {
  return `
    <div class="detail-grid">
      ${rows
        .map(
          ([label, value]) => `
            <div class="detail-card">
              <strong>${escapeHtml(label)}</strong>
              <code>${escapeHtml(String(value ?? ""))}</code>
            </div>
          `,
        )
        .join("")}
    </div>
  `;
}

function renderSkillsSummary(payload) {
  const catalog = extractSkillCatalog(payload);
  return `
    <section class="detail-section">
      <h3>Identity</h3>
      ${renderKeyValueSummary([
        ["session_id", catalog.sessionId],
        ["phase", catalog.phase],
      ])}
    </section>
    <section class="detail-section">
      <h3>Active Skills</h3>
      ${renderBadgeList(catalog.activeSkills, "No active skills")}
    </section>
    <section class="detail-section">
      <h3>Catalog</h3>
      <div class="catalog-list">
        ${
          catalog.skills.length === 0
            ? `<div class="detail-empty-inline">No discovered skills.</div>`
            : catalog.skills
              .map((skill) => `
                <div class="catalog-row">
                  <div class="catalog-main">
                    <strong>${escapeHtml(skill.name)}</strong>
                    <div class="catalog-copy">${escapeHtml(skill.short_description || "No short description")}</div>
                  </div>
                  <div class="catalog-meta">
                    ${badgeHtml({text: skill.source, tone: ""})}
                    ${badgeHtml({text: skill.catalog_visible ? "catalog" : "hidden", tone: ""})}
                    ${skill.body_line_count > 0 ? badgeHtml({text: `${skill.body_line_count} lines`, tone: ""}) : ""}
                    ${catalog.activeSkills.includes(skill.name) ? badgeHtml({text: "active", tone: "phase-active"}) : ""}
                  </div>
                </div>
              `)
              .join("")
        }
      </div>
    </section>
    ${
      catalog.warnings.length > 0
        ? `
          <section class="detail-section">
            <h3>Warnings</h3>
            <div class="warning-list">
              ${catalog.warnings.map((warning) => `<div class="warning-item">${escapeHtml(warning)}</div>`).join("")}
            </div>
          </section>
        `
        : ""
    }
  `;
}

function renderToolParameterSummaryBadges(tool) {
  const entries = schemaPropertyEntries(tool.parametersSchema);
  if (entries.length === 0) {
    return `<div class="detail-empty-inline">No parameters.</div>`;
  }
  return `
    <div class="inline-badges">
      ${entries.map(([name]) => badgeHtml({
        text: tool.requiredParameters.includes(name) ? `${name}*` : name,
        tone: "",
      })).join("")}
    </div>
  `;
}

function renderToolParameterTable(tool) {
  const entries = schemaPropertyEntries(tool.parametersSchema);
  if (entries.length === 0) {
    return `<div class="detail-empty-inline">No parameters.</div>`;
  }
  return `
    <div class="tool-parameter-table">
      <div class="tool-parameter-head">
        <span>Name</span>
        <span>Type</span>
        <span>Required</span>
        <span>Description</span>
        <span>Defaults</span>
      </div>
      ${entries.map(([name, definition]) => `
        <div class="tool-parameter-row">
          <code>${escapeHtml(name)}</code>
          <span>${escapeHtml(parameterTypeLabel(definition))}</span>
          <span>${tool.requiredParameters.includes(name) ? "yes" : "no"}</span>
          <span>${escapeHtml(String(definition?.description || ""))}</span>
          <span>${escapeHtml(
            Array.isArray(definition?.enum) && definition.enum.length > 0
              ? `enum: ${definition.enum.join(", ")}`
              : (definition?.default !== undefined ? `default: ${String(definition.default)}` : "")
          )}</span>
        </div>
      `).join("")}
    </div>
  `;
}

function renderToolCatalogRow(tool) {
  return `
    <div class="catalog-row tool-catalog-row">
      <div class="catalog-main">
        <strong>${escapeHtml(tool.displayName)}</strong>
        <div class="catalog-copy">${escapeHtml(tool.name)}</div>
        <div class="catalog-copy">${escapeHtml(tool.description || "No description")}</div>
        <details class="tool-schema-preview">
          <summary>${escapeHtml(previewParameterNames(tool))}</summary>
          ${renderToolParameterSummaryBadges(tool)}
        </details>
      </div>
      <div class="catalog-meta">
        ${badgeHtml({text: tool.source, tone: ""})}
        ${tool.server ? badgeHtml({text: tool.server, tone: ""}) : ""}
        ${badgeHtml({text: `${tool.parameterCount} params`, tone: ""})}
        ${badgeHtml({text: `${tool.requiredParameters.length} required`, tone: ""})}
      </div>
    </div>
  `;
}

function renderToolsSummary(payload) {
  const catalog = extractToolCatalog(payload);
  const builtinTools = catalog.tools.filter((tool) => tool.source === "builtin");
  const mcpGroups = {};
  catalog.tools.filter((tool) => tool.source === "mcp").forEach((tool) => {
    const key = tool.server || "unknown";
    if (!mcpGroups[key]) {
      mcpGroups[key] = [];
    }
    mcpGroups[key].push(tool);
  });
  const mcpGroupEntries = Object.entries(mcpGroups).sort((left, right) => left[0].localeCompare(right[0]));

  return `
    <section class="detail-section">
      <h3>Identity</h3>
      ${renderKeyValueSummary([
        ["session_id", catalog.sessionId],
        ["phase", catalog.phase],
      ])}
    </section>
    <section class="detail-section">
      <h3>Builtin Tools</h3>
      <div class="catalog-list">
        ${
          builtinTools.length === 0
            ? `<div class="detail-empty-inline">No builtin tools registered.</div>`
            : builtinTools
              .map((tool) => renderToolCatalogRow(tool))
              .join("")
        }
      </div>
    </section>
    <section class="detail-section">
      <h3>MCP Tools</h3>
      <div class="catalog-list">
        ${
          mcpGroupEntries.length === 0
            ? `<div class="detail-empty-inline">No MCP tools registered.</div>`
            : mcpGroupEntries
              .map(([server, tools]) => `
                <div class="group-block">
                  <div class="group-title">${escapeHtml(server)}</div>
                  ${tools.map((tool) => renderToolCatalogRow(tool)).join("")}
                </div>
              `)
              .join("")
        }
      </div>
    </section>
    ${
      catalog.tools.length === 0
        ? `<section class="detail-section"><div class="detail-empty-inline">No tools registered.</div></section>`
        : ""
    }
  `;
}

function renderSkillItemSummary(payload) {
  return `
    <section class="detail-section">
      <h3>Skill</h3>
      ${renderKeyValueSummary([
        ["name", payload.metadata?.name],
        ["session_id", payload.spec?.session_id],
        ["source", payload.spec?.source],
        ["short_description", payload.spec?.short_description || ""],
        ["active", payload.status?.active ? "true" : "false"],
        ["catalog_visible", payload.spec?.catalog_visible ? "true" : "false"],
        ["body_line_count", payload.spec?.body_line_count ?? 0],
      ])}
    </section>
  `;
}

function renderMemoryWorkspaceSummary(payload) {
  return `
    <section class="detail-section">
      <h3>Memory Workspace</h3>
      ${renderKeyValueSummary([
        ["session_id", payload.spec?.session_id],
        ["phase", payload.status?.phase],
        ["mode", payload.status?.mode || ""],
        ["root_dir", payload.spec?.root_dir || ""],
        ["document_path", payload.spec?.document_path || ""],
        ["settings_path", payload.spec?.settings_path || ""],
        ["audit_path", payload.spec?.audit_path || ""],
        ["entry_count", payload.status?.entry_count || 0],
        ["daily_file_count", payload.status?.daily_file_count || 0],
      ])}
    </section>
    ${
      payload.status?.section_counts
        ? `
          <section class="detail-section">
            <h3>Entry Counts</h3>
            ${renderObjectCards(payload.status.section_counts, "Sections")}
            ${renderObjectCards(payload.status.status_counts || {}, "Lifecycle")}
          </section>
        `
        : ""
    }
    ${
      Array.isArray(payload.status?.daily_files) && payload.status.daily_files.length > 0
        ? `
          <section class="detail-section">
            <h3>Daily Logs</h3>
            <div class="catalog-list">
              ${payload.status.daily_files.map((date) => `
                <div class="catalog-row">
                  <div class="catalog-main"><strong>${escapeHtml(date)}</strong></div>
                </div>
              `).join("")}
            </div>
          </section>
        `
        : ""
    }
  `;
}

function renderMemoryEntrySummary(payload) {
  return `
    <section class="detail-section">
      <h3>Curated Entry</h3>
      ${renderKeyValueSummary([
        ["entry_id", payload.metadata?.name],
        ["session_id", payload.spec?.session_id],
        ["kind", payload.spec?.kind],
        ["title", payload.spec?.title],
        ["status", payload.status?.status],
        ["source", payload.spec?.source || ""],
        ["confidence", payload.status?.confidence ?? ""],
        ["created_at", payload.spec?.created_at || ""],
        ["updated_at", payload.spec?.updated_at || ""],
        ["last_verified_at", payload.spec?.last_verified_at || ""],
        ["supersedes", payload.spec?.supersedes || ""],
      ])}
    </section>
    <section class="detail-section">
      <h3>Content</h3>
      <pre>${escapeHtml(payload.status?.content || "")}</pre>
    </section>
  `;
}

function renderMemorySettingsSummary(payload) {
  return `
    <section class="detail-section">
      <h3>Memory Settings</h3>
      ${renderKeyValueSummary([
        ["session_id", payload.spec?.session_id],
        ["path", payload.spec?.path || ""],
        ["mode", payload.status?.mode],
        ["auto_retrieve_enabled", payload.status?.auto_retrieve_enabled ? "true" : "false"],
        ["manual_write_enabled", payload.status?.manual_write_enabled ? "true" : "false"],
        ["autonomous_write_enabled", payload.status?.autonomous_write_enabled ? "true" : "false"],
      ])}
    </section>
  `;
}

function renderMemoryAuditSummary(payload) {
  const items = Array.isArray(payload?.items) ? payload.items : [];
  if (items.length === 0) {
    return `<div class="detail-empty">No memory audit events yet.</div>`;
  }
  return `
    <section class="detail-section">
      <h3>Memory Audit</h3>
      <div class="catalog-list">
        ${items.map((item) => `
          <div class="catalog-row">
            <div class="catalog-main">
              <strong>${escapeHtml(item.spec?.event || "event")}</strong>
              <div class="catalog-meta">${escapeHtml(item.spec?.timestamp || "")}</div>
            </div>
            <div class="catalog-badges">
              ${badgeHtml({text: item.spec?.event || "event", tone: ""})}
            </div>
          </div>
        `).join("")}
      </div>
    </section>
  `;
}

function renderMemoryDocumentSummary(payload) {
  return `
    <section class="detail-section">
      <h3>Curated Memory</h3>
      ${renderKeyValueSummary([
        ["session_id", payload.spec?.session_id],
        ["path", payload.spec?.path],
        ["phase", payload.status?.phase],
      ])}
    </section>
    <section class="detail-section">
      <h3>Markdown</h3>
      <pre>${escapeHtml(payload.status?.content || "")}</pre>
    </section>
  `;
}

function renderMemoryDailyLogSummary(payload) {
  return `
    <section class="detail-section">
      <h3>Daily Log</h3>
      ${renderKeyValueSummary([
        ["session_id", payload.spec?.session_id],
        ["date", payload.spec?.date],
        ["path", payload.spec?.path],
        ["phase", payload.status?.phase],
      ])}
    </section>
    <section class="detail-section">
      <h3>Markdown</h3>
      <pre>${escapeHtml(payload.status?.content || "")}</pre>
    </section>
  `;
}

function renderToolItemSummary(payload) {
  return `
    <section class="detail-section">
      <h3>Tool</h3>
      ${renderKeyValueSummary([
        ["display_name", payload.spec?.display_name],
        ["tool_name", payload.spec?.tool_name],
        ["session_id", payload.spec?.session_id],
        ["source", payload.spec?.source],
        ["group", payload.spec?.group],
        ["server", payload.spec?.server || ""],
        ["parameter_count", payload.spec?.parameter_count || 0],
      ])}
    </section>
    <section class="detail-section">
      <h3>Description</h3>
      <div class="detail-card">
        <code>${escapeHtml(payload.spec?.description || "No description")}</code>
      </div>
    </section>
    <section class="detail-section">
      <h3>Required Parameters</h3>
      ${renderBadgeList(payload.spec?.required_parameters || [], "No required parameters")}
    </section>
    <section class="detail-section">
      <h3>Parameter Schema</h3>
      ${renderToolParameterTable({
        parametersSchema: payload.spec?.parameters_schema,
        requiredParameters: Array.isArray(payload.spec?.required_parameters) ? payload.spec.required_parameters : [],
      })}
    </section>
  `;
}

function renderSummaryTab(node, payload) {
  if (node.error) {
    return `
      <div class="detail-empty">
        <strong>Load error</strong><br>
        ${escapeHtml(node.error)}
      </div>
    `;
  }
  if (!payload) {
    return `<div class="detail-empty">Select a resource in the tree to inspect it.</div>`;
  }
  if (node.kind === "skills") {
    return renderSkillsSummary(payload);
  }
  if (node.kind === "memory") {
    return renderMemoryWorkspaceSummary(payload);
  }
  if (node.kind === "memory-document") {
    return renderMemoryDocumentSummary(payload);
  }
  if (node.kind === "memory-entry") {
    return renderMemoryEntrySummary(payload);
  }
  if (node.kind === "memory-daily-file") {
    return renderMemoryDailyLogSummary(payload);
  }
  if (node.kind === "memory-settings") {
    return renderMemorySettingsSummary(payload);
  }
  if (node.kind === "memory-audit") {
    return renderMemoryAuditSummary(payload);
  }
  if (node.kind === "tools") {
    return renderToolsSummary(payload);
  }
  if (node.kind === "skill-item") {
    return renderSkillItemSummary(payload);
  }
  if (node.kind === "tool-item") {
    return renderToolItemSummary(payload);
  }
  if (payload.kind && String(payload.kind).endsWith("List")) {
    return renderListSummary(payload);
  }
  return [
    renderObjectCards(
      {
        apiVersion: payload.apiVersion,
        kind: payload.kind,
        name: payload.metadata?.name,
        resourceVersion: payload.metadata?.resourceVersion,
      },
      "Identity",
    ),
    renderObjectCards(payload.status || {}, "Status"),
    renderObjectCards(payload.spec || {}, "Spec"),
  ].join("");
}

function relatedNodesForSelected(node) {
  const related = [];
  if (node.parentId) {
    const parent = getNode(node.parentId);
    if (parent) {
      related.push({
        relation: "Parent",
        targetId: parent.id,
        label: parent.label,
        meta: parent.meta || parent.kind,
      });
    }
  }
  node.childIds.forEach((childId) => {
    const child = getNode(childId);
    if (!child) {
      return;
    }
    related.push({
      relation: "Child",
      targetId: child.id,
      label: child.label,
      meta: child.meta || child.kind,
    });
  });
  if (node.parentId) {
    const parent = getNode(node.parentId);
    parent?.childIds
      .filter((childId) => childId !== node.id)
      .forEach((childId) => {
        const sibling = getNode(childId);
        if (!sibling) {
          return;
        }
        related.push({
          relation: "Sibling",
          targetId: sibling.id,
          label: sibling.label,
          meta: sibling.meta || sibling.kind,
        });
      });
  }
  return related;
}

function renderRelatedTab(node) {
  const related = relatedNodesForSelected(node);
  if (related.length === 0) {
    return `<div class="detail-empty">No related nodes for the current selection.</div>`;
  }
  return `
    <section class="detail-section">
      <h3>Related</h3>
      <div class="related-list">
        ${related
          .map(
            (item) => `
              <div class="related-item">
                <strong>${escapeHtml(item.relation)}: ${escapeHtml(item.label)}</strong>
                <div>${escapeHtml(item.meta)}</div>
                <button type="button" class="secondary" data-related-node="${escapeHtml(item.targetId)}">
                  Open
                </button>
              </div>
            `,
          )
          .join("")}
      </div>
    </section>
  `;
}

function renderRawTab(payload) {
  if (!payload) {
    return `<div class="detail-empty">No payload loaded.</div>`;
  }
  return `<pre>${escapeHtml(JSON.stringify(payload, null, 2))}</pre>`;
}

function renderDetailHeader(node, payload) {
  const badges = [...(node.badges || [])];
  if (node.stale) {
    badges.unshift({text: "stale", tone: "phase-busy"});
  }
  if (node.error) {
    badges.unshift({text: "error", tone: "phase-error"});
  }
  elements.detailHeader.innerHTML = `
    <div class="detail-title-row">
      <div class="detail-title-copy">
        <div class="eyebrow">${escapeHtml(node.kind)}</div>
        <h2>${escapeHtml(node.label)}</h2>
        <p>${escapeHtml(node.meta || payload?.metadata?.name || "")}</p>
      </div>
      <div class="tree-badges">${badges.map(badgeHtml).join("")}</div>
    </div>
  `;
}

function renderDetail() {
  const node = getNode(state.selectedNodeId);
  const payload = currentPayload();
  if (!node) {
    elements.detailHeader.innerHTML = "";
    elements.detailContent.innerHTML = `<div class="detail-empty">No selection.</div>`;
    return;
  }

  renderDetailHeader(node, payload);

  if (state.selectedTab === "summary") {
    elements.detailContent.innerHTML = renderSummaryTab(node, payload);
  } else if (state.selectedTab === "related") {
    elements.detailContent.innerHTML = renderRelatedTab(node);
  } else {
    elements.detailContent.innerHTML = renderRawTab(payload);
  }

  elements.detailContent.querySelectorAll("[data-related-node]").forEach((button) => {
    button.addEventListener("click", async () => {
      const nodeId = button.getAttribute("data-related-node");
      await selectNode(nodeId);
    });
  });
}

function renderTabs() {
  elements.detailTabs.querySelectorAll(".tab").forEach((tab) => {
    const tabName = tab.getAttribute("data-tab");
    tab.classList.toggle("active", tabName === state.selectedTab);
  });
}

function renderAll() {
  renderNav();
  renderTree();
  renderTabs();
  renderDetail();
}

function applyRuntimeListSnapshot(payload) {
  setCacheEntry("runtime-snapshot-list", {
    status: "ready",
    data: payload,
    loadedAt: Date.now(),
    stale: false,
    error: null,
  });
  Object.values(state.sessionsIndex).forEach((sessionItem) => {
    const sessionNode = getNode(`session:${sessionItem.metadata?.name}`);
    if (!sessionNode) {
      return;
    }
    updateNodeBadges(sessionNode.id, sessionNodeBadges(sessionItem));
  });
}

function markNodesForResource(resource) {
  Object.values(state.nodesById).forEach((node) => {
    let affected = false;
    if (resource === "overview" && node.kind === "overview") {
      affected = true;
    }
    if (resource === "sessions" && ["sessions-root", "session", "session-detail", "context"].includes(node.kind)) {
      affected = true;
    }
    if (resource === "runtimes" && ["session", "runtime", "agent", "context"].includes(node.kind)) {
      affected = true;
    }
    if (resource === "runtimes" && ["skills", "tools", "skill-item", "tool-item"].includes(node.kind)) {
      affected = true;
    }
    if (resource === "turns" && ["memory", "memory-document", "memory-entry-list", "memory-entry", "memory-daily-list", "memory-daily-file", "memory-settings", "memory-audit"].includes(node.kind)) {
      affected = true;
    }
    if (resource === "turns" && ["turns-root", "turns", "turn"].includes(node.kind)) {
      affected = true;
    }
    if (resource === "event-bus" && node.kind === "event-bus") {
      affected = true;
    }
    if (resource === "config" && node.kind === "config") {
      affected = true;
    }
    if (affected) {
      node.stale = true;
      const cacheKey = cacheKeyForNode(node);
      const entry = getCacheEntry(cacheKey);
      if (entry.status === "ready") {
        setCacheEntry(cacheKey, {stale: true});
      }
    }
  });
}

async function refreshSelectedIfAffected(resource) {
  const node = getNode(state.selectedNodeId);
  if (!node) {
    return;
  }
  const affected =
    (resource === "sessions" && ["session", "session-detail", "context"].includes(node.kind)) ||
    (resource === "runtimes" && ["runtime", "agent", "context", "skills", "tools", "skill-item", "tool-item"].includes(node.kind)) ||
    (resource === "turns" && ["memory", "memory-document", "memory-entry-list", "memory-entry", "memory-daily-list", "memory-daily-file", "memory-settings", "memory-audit"].includes(node.kind)) ||
    (resource === "turns" && ["turns-root", "turns", "turn"].includes(node.kind)) ||
    (resource === "overview" && node.kind === "overview") ||
    (resource === "event-bus" && node.kind === "event-bus") ||
    (resource === "config" && node.kind === "config");

  if (!affected) {
    renderAll();
    return;
  }

  try {
    await loadNodeData(node.id, {force: true});
  } catch (_error) {
    renderAll();
  }
}

async function handleStreamSnapshot(envelope) {
  const resource = envelope.resource;
  const payload = envelope.payload;
  if (resource === "overview") {
    setCacheEntry("overview", {
      status: "ready",
      data: payload,
      loadedAt: Date.now(),
      stale: false,
      error: null,
    });
    const overviewNode = getNode("overview");
    if (overviewNode) {
      overviewNode.stale = false;
      applyPayloadToNode("overview", payload);
    }
  } else if (resource === "sessions") {
    setCacheEntry("sessions-root", {
      status: "ready",
      data: payload,
      loadedAt: Date.now(),
      stale: false,
      error: null,
    });
    const sessionsRoot = getNode("sessions-root");
    if (sessionsRoot) {
      sessionsRoot.stale = false;
      applyPayloadToNode("sessions-root", payload);
    }
  } else if (resource === "runtimes") {
    applyRuntimeListSnapshot(payload);
  } else if (resource === "turns") {
    setCacheEntry("turns-root", {
      status: "ready",
      data: payload,
      loadedAt: Date.now(),
      stale: false,
      error: null,
    });
    const turnsRoot = getNode("turns-root");
    if (turnsRoot && state.expandedNodeIds.has("turns-root")) {
      turnsRoot.stale = false;
      applyPayloadToNode("turns-root", payload);
    }
  } else if (resource === "event-bus") {
    setCacheEntry("event-bus", {
      status: "ready",
      data: payload,
      loadedAt: Date.now(),
      stale: false,
      error: null,
    });
    const busNode = getNode("event-bus");
    if (busNode) {
      busNode.stale = false;
      applyPayloadToNode("event-bus", payload);
    }
  } else if (resource === "config") {
    setCacheEntry("config", {
      status: "ready",
      data: payload,
      loadedAt: Date.now(),
      stale: false,
      error: null,
    });
    const configNode = getNode("config");
    if (configNode) {
      configNode.stale = false;
      applyPayloadToNode("config", payload);
    }
  }
  await refreshSelectedIfAffected(resource);
}

function connectStream() {
  if (state.eventSource) {
    state.eventSource.close();
  }
  const source = new EventSource(`/api/v1/admin/stream?resources=${encodeURIComponent(STREAM_RESOURCES)}`);
  state.eventSource = source;

  source.addEventListener("snapshot", async (event) => {
    setConnectionState("connected");
    const payload = JSON.parse(event.data);
    await handleStreamSnapshot(payload);
  });

  source.addEventListener("resource_changed", (event) => {
    setConnectionState("connected");
    const payload = JSON.parse(event.data);
    markNodesForResource(payload.resource);
    renderAll();
  });

  source.addEventListener("heartbeat", () => {
    setConnectionState("connected");
  });

  source.addEventListener("error", () => {
    setConnectionState("stale");
  });

  source.onerror = () => {
    setConnectionState("stale");
  };
}

function installTabHandlers() {
  elements.detailTabs.querySelectorAll(".tab").forEach((tab) => {
    tab.addEventListener("click", () => {
      const tabName = tab.getAttribute("data-tab");
      if (!DETAIL_TABS.includes(tabName)) {
        return;
      }
      state.selectedTab = tabName;
      renderTabs();
      renderDetail();
    });
  });
}

async function refreshCurrentView() {
  const activeRoot = getNode(state.activeRootId);
  if (activeRoot) {
    try {
      await loadNodeData(activeRoot.id, {force: true});
    } catch (_error) {
      // Root error is reflected in UI state.
    }
  }
  if (state.selectedNodeId && state.selectedNodeId !== state.activeRootId) {
    try {
      await loadNodeData(state.selectedNodeId, {force: true});
    } catch (_error) {
      // Detail error is reflected in UI state.
    }
  }
  renderAll();
}

async function bootstrap() {
  initRoots();
  state.expandedNodeIds.add("sessions-root");
  renderAll();
  await Promise.all([ensureRootLoaded("overview"), ensureRootLoaded("sessions-root")]);
  renderAll();
  connectStream();
  installTabHandlers();
  elements.refreshButton.addEventListener("click", refreshCurrentView);
  await selectNode("overview");
}

bootstrap().catch((error) => {
  const message = error instanceof Error ? error.message : String(error);
  elements.detailContent.innerHTML = `
    <div class="detail-empty">
      <strong>Bootstrap error</strong><br>
      ${escapeHtml(message)}
    </div>
  `;
});
