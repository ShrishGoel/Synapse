const HISTORY_KEY = "domHistory";
const RETENTION_MS = 30 * 60 * 1000;

async function getHistory() {
  const data = await chrome.storage.local.get({ [HISTORY_KEY]: [] });
  return Array.isArray(data[HISTORY_KEY]) ? data[HISTORY_KEY] : [];
}

async function saveHistory(history) {
  await chrome.storage.local.set({ [HISTORY_KEY]: history });
}

function pruneHistory(history, now = Date.now()) {
  return history.filter((entry) => now - entry.timestamp <= RETENTION_MS);
}

async function addHistoryEntry(entry) {
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
    readableLength:
      typeof entry.readable?.textContent === "string" ? entry.readable.textContent.length : 0
  };

  if (existingIndex >= 0) {
    history[existingIndex] = nextEntry;
  } else {
    history.push(nextEntry);
  }

  await saveHistory(history);
}

chrome.runtime.onMessage.addListener((message, sender, sendResponse) => {
  if (message?.type === "DOM_SNAPSHOT") {
    addHistoryEntry({
      tabId: sender.tab?.id,
      url: message.url || sender.tab?.url || "",
      title: message.title || sender.tab?.title || "",
      dom: message.dom || "",
      readable: message.readable || null
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

  if (message?.type === "CLEAR_HISTORY") {
    saveHistory([])
      .then(() => sendResponse({ ok: true }))
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
