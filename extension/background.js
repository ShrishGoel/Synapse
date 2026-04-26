const HISTORY_KEY = "domHistory";
const RETENTION_MS = 30 * 60 * 1000;
const BACKEND_SNAPSHOT_URL = "http://127.0.0.1:8010/api/v1/extension/snapshot";
const HISTORY_META_LIMIT = 40;

function isHttpUrl(url) {
  return typeof url === "string" && /^https?:\/\//i.test(url);
}

function shouldIgnoreUrl(url) {
  if (!isHttpUrl(url)) {
    return true;
  }

  try {
    const parsed = new URL(url);
    return ["localhost", "127.0.0.1"].includes(parsed.hostname);
  } catch {
    return true;
  }
}

async function pushSnapshotToBackend(entry) {
  if (shouldIgnoreUrl(entry?.url) || !entry?.dom) {
    return false;
  }

  try {
    const response = await fetch(BACKEND_SNAPSHOT_URL, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        url: entry.url,
        title: entry.title || "",
        dom: entry.dom,
        readable_text: entry.readable?.textContent || "",
        readable_html: entry.readable?.content || "",
        readable_extractor: entry.readable?.extractor || "",
        timestamp: Math.floor((entry.timestamp || Date.now()) / 1000),
      }),
    });
    return response.ok;
  } catch (_) {
    return false;
  }
}

async function getHistory() {
  const data = await chrome.storage.local.get({ [HISTORY_KEY]: [] });
  return Array.isArray(data[HISTORY_KEY]) ? data[HISTORY_KEY] : [];
}

function pruneHistory(history, now = Date.now()) {
  return history.filter((entry) => now - entry.timestamp <= RETENTION_MS);
}

async function saveHistory(history) {
  await chrome.storage.local.set({ [HISTORY_KEY]: history });
}

async function getHistoryMeta() {
  const history = pruneHistory(await getHistory());
  return history.slice(-HISTORY_META_LIMIT).map((entry) => ({
    id: entry.id,
    timestamp: entry.timestamp,
    updatedAt: entry.updatedAt,
    tabId: entry.tabId,
    url: entry.url,
    title: entry.title,
    domLength: entry.domLength || 0,
    readableLength: entry.readableLength || 0,
    extractor: entry.readable?.extractor || "none",
  }));
}

async function addHistoryEntry(entry) {
  if (shouldIgnoreUrl(entry?.url)) {
    return;
  }

  const now = Date.now();
  const history = pruneHistory(await getHistory(), now);
  const existingIndex = history.findIndex((item) => item.url === entry.url);
  const nextEntry = {
    id: existingIndex >= 0 ? history[existingIndex].id : crypto.randomUUID(),
    timestamp: now,
    updatedAt: now,
    tabId: entry.tabId,
    url: entry.url,
    title: entry.title || "",
    dom: entry.dom || "",
    domLength: typeof entry.dom === "string" ? entry.dom.length : 0,
    readable: entry.readable || null,
    readableLength: typeof entry.readable?.textContent === "string" ? entry.readable.textContent.length : 0,
  };

  if (existingIndex >= 0) {
    history[existingIndex] = nextEntry;
  } else {
    history.push(nextEntry);
  }

  await saveHistory(history);
  await pushSnapshotToBackend(nextEntry);
}

async function syncHistoryToBackend() {
  const history = pruneHistory(await getHistory());
  await saveHistory(history);
  let uploaded = 0;

  for (const entry of history) {
    if (await pushSnapshotToBackend(entry)) {
      uploaded += 1;
    }
  }

  return {
    synced: uploaded,
    attempted: history.length,
    backendAvailable: uploaded > 0 || history.length === 0,
  };
}

chrome.runtime.onMessage.addListener((message, sender, sendResponse) => {
  if (message?.type === "DOM_SNAPSHOT") {
    addHistoryEntry({
      tabId: sender.tab?.id,
      url: message.url || sender.tab?.url || "",
      title: message.title || sender.tab?.title || "",
      dom: message.dom || "",
      readable: message.readable || null,
    })
      .then(() => sendResponse({ ok: true }))
      .catch((error) => sendResponse({ ok: false, error: error.message }));
    return true;
  }

  if (message?.type === "GET_HISTORY") {
    getHistory()
      .then((history) => {
        const pruned = pruneHistory(history);
        if (pruned.length !== history.length) {
          saveHistory(pruned);
        }
        sendResponse({ ok: true, history: pruned });
      })
      .catch((error) => sendResponse({ ok: false, error: error.message }));
    return true;
  }

  if (message?.type === "GET_HISTORY_META") {
    getHistoryMeta()
      .then((history) => sendResponse({ ok: true, history }))
      .catch((error) => sendResponse({ ok: false, error: error.message }));
    return true;
  }

  if (message?.type === "GET_HISTORY_ENTRY") {
    getHistory()
      .then((history) => {
        const entry = history.find((item) => item.id === message.id);
        if (!entry) {
          throw new Error("Snapshot not found");
        }
        sendResponse({ ok: true, entry });
      })
      .catch((error) => sendResponse({ ok: false, error: error.message }));
    return true;
  }

  if (message?.type === "CLEAR_HISTORY") {
    saveHistory([])
      .then(() => sendResponse({ ok: true }))
      .catch((error) => sendResponse({ ok: false, error: error.message }));
    return true;
  }

  if (message?.type === "SYNC_HISTORY_TO_BACKEND") {
    syncHistoryToBackend()
      .then((payload) => sendResponse({ ok: true, ...payload }))
      .catch((error) => sendResponse({ ok: false, error: error.message }));
    return true;
  }

  return false;
});

chrome.alarms?.create?.("prune-history", { periodInMinutes: 1 });

chrome.alarms?.onAlarm?.addListener((alarm) => {
  if (alarm.name !== "prune-history") {
    return;
  }

  getHistory()
    .then((history) => saveHistory(pruneHistory(history)))
    .catch(() => {});
});
