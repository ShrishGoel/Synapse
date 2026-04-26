const summary = document.querySelector("#summary");
const clearButton = document.querySelector("#clearButton");
const historyList = document.querySelector("#historyList");
const emptyState = document.querySelector("#emptyState");
const template = document.querySelector("#historyItemTemplate");
const promptForm = document.querySelector("#promptForm");
const promptInput = document.querySelector("#promptInput");
const discoveryToggle = document.querySelector("#discoveryToggle");
const statusToast = document.querySelector("#statusToast");
const toastMessage = document.querySelector("#toastMessage");
const themeButton = document.querySelector("#themeButton");

const FRONTEND_URL = "http://localhost:5173";
const BACKEND_BASE_URL = "http://127.0.0.1:8010";
const DEFAULT_PROMPT = "Compare the items I've been looking at";
const PROMPT_STORAGE_KEY = "synapseUserPrompt";
const DISCOVERY_STORAGE_KEY = "synapseEnableDiscovery";
const THEME_STORAGE_KEY = "synapsePopupTheme";
let shouldClearPromptOnFocus = false;
let hasPromptBeenTouched = false;

function formatTime(timestamp) {
  return new Intl.DateTimeFormat(undefined, {
    hour: "numeric",
    minute: "2-digit",
    second: "2-digit",
  }).format(new Date(timestamp));
}

function formatBytes(length) {
  if (length < 1024) {
    return `${length} B`;
  }

  if (length < 1024 * 1024) {
    return `${(length / 1024).toFixed(1)} KB`;
  }

  return `${(length / (1024 * 1024)).toFixed(1)} MB`;
}

function getPreviewText(entry) {
  return entry.readable?.textContent || entry.readable?.excerpt || entry.dom || "";
}

function getDisplayTitle(entry) {
  return entry.readable?.title || entry.title || "Untitled page";
}

function getExtractorLabel(entry) {
  return entry.readable?.extractor || "none";
}

function sendMessage(message) {
  return new Promise((resolve, reject) => {
    chrome.runtime.sendMessage(message, (response) => {
      if (chrome.runtime.lastError) {
        reject(chrome.runtime.lastError);
        return;
      }

      if (!response?.ok) {
        reject(new Error(response?.error || "Unknown extension error"));
        return;
      }

      resolve(response);
    });
  });
}

async function loadStoredPrompt() {
  const data = await chrome.storage.local.get({ [PROMPT_STORAGE_KEY]: DEFAULT_PROMPT });
  const savedPrompt = data?.[PROMPT_STORAGE_KEY];
  return typeof savedPrompt === "string" && savedPrompt.trim() ? savedPrompt.trim() : DEFAULT_PROMPT;
}

async function saveStoredPrompt(prompt) {
  await chrome.storage.local.set({ [PROMPT_STORAGE_KEY]: prompt });
}

async function loadStoredDiscoveryEnabled() {
  const data = await chrome.storage.local.get({ [DISCOVERY_STORAGE_KEY]: false });
  return Boolean(data?.[DISCOVERY_STORAGE_KEY]);
}

async function saveStoredDiscoveryEnabled(enabled) {
  await chrome.storage.local.set({ [DISCOVERY_STORAGE_KEY]: Boolean(enabled) });
}

function setPromptValue(value, { clearOnFocus = false, preserveUserInput = false } = {}) {
  if (preserveUserInput && hasPromptBeenTouched) {
    return;
  }

  promptInput.value = value;
  shouldClearPromptOnFocus = clearOnFocus && Boolean(value);
}

function clearPromptForEditing() {
  if (!shouldClearPromptOnFocus) {
    return;
  }

  promptInput.value = "";
  shouldClearPromptOnFocus = false;
  hasPromptBeenTouched = true;
}

function applyTheme(theme) {
  const resolvedTheme = theme === "light" ? "light" : "dark";
  document.documentElement.dataset.theme = resolvedTheme;
  document.body.dataset.theme = resolvedTheme;
  document.documentElement.classList.toggle("light-theme", resolvedTheme === "light");
  document.body.classList.toggle("light-theme", resolvedTheme === "light");
  document.documentElement.style.colorScheme = resolvedTheme;
  document.body.style.colorScheme = resolvedTheme;
  document.body.style.backgroundColor = resolvedTheme === "light" ? "#f7f7f4" : "#0a0a0a";
  document.body.style.color = resolvedTheme === "light" ? "#172033" : "#e5e7eb";
  themeButton?.setAttribute("aria-label", `Switch to ${resolvedTheme === "light" ? "dark" : "light"} theme`);
  themeButton?.setAttribute("title", `Switch to ${resolvedTheme === "light" ? "dark" : "light"} theme`);
}

async function loadTheme() {
  const data = await chrome.storage.local.get({ [THEME_STORAGE_KEY]: "dark" });
  applyTheme(data[THEME_STORAGE_KEY]);
}

async function toggleTheme() {
  const nextTheme = document.documentElement.dataset.theme === "light" ? "dark" : "light";
  applyTheme(nextTheme);
  await chrome.storage.local.set({ [THEME_STORAGE_KEY]: nextTheme });
}

function showToast(message, durationMs = 2500) {
  toastMessage.textContent = message;
  statusToast.hidden = false;
  statusToast.offsetHeight;
  statusToast.classList.add("visible");

  setTimeout(() => {
    statusToast.classList.remove("visible");
    setTimeout(() => {
      statusToast.hidden = true;
    }, 300);
  }, durationMs);
}

function render(history) {
  historyList.textContent = "";
  emptyState.hidden = history.length !== 0;
  summary.textContent = `${history.length} snapshot${history.length === 1 ? "" : "s"} from the last 30 minutes`;

  for (const entry of history.toReversed()) {
    const fragment = template.content.cloneNode(true);
    const item = fragment.querySelector("li");
    const button = fragment.querySelector(".entryButton");
    const title = fragment.querySelector(".entryTitle");
    const expandedUrl = fragment.querySelector(".entryExpandedUrl");
    const expandedMeta = fragment.querySelector(".entryExpandedMeta");
    const preview = fragment.querySelector(".domPreview");
    const copyButton = fragment.querySelector(".copy-button");

    title.textContent = getDisplayTitle(entry);
    expandedUrl.textContent = entry.url;
    expandedMeta.textContent = `${formatTime(entry.timestamp)} - ${getExtractorLabel(entry)} - readable ${formatBytes(entry.readableLength || 0)} - dom ${formatBytes(entry.domLength || entry.dom?.length || 0)}`;
    preview.value = "";

    button.addEventListener("click", () => {
      const isOpen = item.dataset.open === "true";
      document.querySelectorAll('.history-card[data-open="true"]').forEach((openItem) => {
        if (openItem !== item) {
          openItem.dataset.open = "false";
          const openPreview = openItem.querySelector(".domPreview");
          if (openPreview) {
            openPreview.value = "";
          }
        }
      });

      item.dataset.open = isOpen ? "false" : "true";
      preview.value = isOpen ? "" : getPreviewText(entry);

      if (!isOpen) {
        item.scrollIntoView({ behavior: "smooth", block: "nearest" });
      }
    });

    copyButton.addEventListener("click", async (event) => {
      event.stopPropagation();

      try {
        await navigator.clipboard.writeText(getPreviewText(entry));
        const label = copyButton.querySelector("span");
        const originalText = label.textContent;
        label.textContent = "Copied!";
        copyButton.classList.add("copied");

        setTimeout(() => {
          label.textContent = originalText;
          copyButton.classList.remove("copied");
        }, 2000);
      } catch (error) {
        console.error("Failed to copy extracted text", error);
      }
    });

    historyList.append(fragment);
  }
}

async function loadHistory() {
  try {
    const response = await sendMessage({ type: "GET_HISTORY" });
    render(response.history || []);
  } catch (error) {
    summary.textContent = error.message;
  }
}

async function loadPrompt() {
  setPromptValue(await loadStoredPrompt(), { clearOnFocus: true, preserveUserInput: true });
  if (discoveryToggle) {
    discoveryToggle.checked = await loadStoredDiscoveryEnabled();
  }

  try {
    const response = await fetch(`${BACKEND_BASE_URL}/api/v1/extension/preferences`);
    if (!response.ok) {
      throw new Error(`Failed to load prompt: ${response.status}`);
    }

    const payload = await response.json();
    const resolvedPrompt = String(payload.user_prompt || "").trim() || promptInput.value || DEFAULT_PROMPT;
    setPromptValue(resolvedPrompt, { clearOnFocus: true, preserveUserInput: true });
    if (discoveryToggle) {
      discoveryToggle.checked = Boolean(payload.enable_discovery);
      await saveStoredDiscoveryEnabled(discoveryToggle.checked);
    }
    await saveStoredPrompt(resolvedPrompt);
  } catch (_) {
    // Keep the popup usable even when the backend is offline.
  }
}

async function openSynapse() {
  const prompt = promptInput.value.trim();
  if (!prompt) {
    showToast("Prompt cannot be empty");
    return;
  }

  try {
    await saveStoredPrompt(prompt);
    await saveStoredDiscoveryEnabled(Boolean(discoveryToggle?.checked));
    summary.textContent = "Preparing graph...";

    let syncPayload = { attempted: 0 };
    try {
      syncPayload = await sendMessage({ type: "SYNC_HISTORY_TO_BACKEND" });
    } catch (_) {
      // Best effort only. The frontend can still open.
    }

    try {
      await fetch(`${BACKEND_BASE_URL}/api/v1/extension/preferences`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          user_prompt: prompt,
          enable_discovery: Boolean(discoveryToggle?.checked),
        }),
      });
    } catch (_) {
      // Query string still carries the prompt to the frontend.
    }

    const attemptedSnapshots = Number(syncPayload.attempted ?? syncPayload.synced ?? 0);
    summary.textContent = `Opening Synapse from ${attemptedSnapshots} captured snapshot${attemptedSnapshots === 1 ? "" : "s"}...`;
    const targetUrl = `${FRONTEND_URL}/?prompt=${encodeURIComponent(prompt)}&discover=${Boolean(discoveryToggle?.checked) ? "1" : "0"}&run=${Date.now()}`;
    await chrome.tabs.create({ url: targetUrl });
    window.close();
  } catch (error) {
    summary.textContent = error.message || "Failed to graph.";
  }
}

promptForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  await openSynapse();
});

promptInput.addEventListener("pointerdown", () => {
  clearPromptForEditing();
});

promptInput.addEventListener("focus", () => {
  clearPromptForEditing();
});

promptInput.addEventListener("input", () => {
  hasPromptBeenTouched = true;
  shouldClearPromptOnFocus = false;
});

document.querySelectorAll(".hint-chip").forEach((chip) => {
  chip.addEventListener("click", () => {
    setPromptValue(chip.textContent, { clearOnFocus: false });
    promptInput.focus();
  });
});

clearButton.addEventListener("click", async () => {
  clearButton.disabled = true;

  try {
    await sendMessage({ type: "CLEAR_HISTORY" });
    render([]);
    showToast("History cleared");
  } catch (error) {
    summary.textContent = error.message;
  } finally {
    clearButton.disabled = false;
  }
});

themeButton?.addEventListener("click", () => {
  toggleTheme().catch((error) => {
    console.error("Failed to toggle theme", error);
  });
});

discoveryToggle?.addEventListener("change", () => {
  saveStoredDiscoveryEnabled(Boolean(discoveryToggle.checked)).catch((error) => {
    console.error("Failed to persist discovery setting", error);
  });
});

loadTheme();
loadPrompt();
loadHistory();
