const summary = document.querySelector("#summary");
const clearButton = document.querySelector("#clearButton");
const historyList = document.querySelector("#historyList");
const emptyState = document.querySelector("#emptyState");
const template = document.querySelector("#historyItemTemplate");
const promptForm = document.querySelector("#promptForm");
const promptInput = document.querySelector("#promptInput");
const statusToast = document.querySelector("#statusToast");
const toastMessage = document.querySelector("#toastMessage");

const FRONTEND_URL = "http://localhost:5173";

function formatTime(timestamp) {
  return new Intl.DateTimeFormat(undefined, {
    hour: "numeric",
    minute: "2-digit",
    second: "2-digit"
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
    const url = fragment.querySelector(".entryUrl");
    const meta = fragment.querySelector(".entryMeta");
    const preview = fragment.querySelector(".domPreview");
    const copyButton = fragment.querySelector(".copy-button");

    title.textContent = getDisplayTitle(entry);
    url.textContent = entry.url;
    meta.textContent = `${formatTime(entry.timestamp)} - ${getExtractorLabel(entry)} - readable ${formatBytes(entry.readableLength || 0)} - dom ${formatBytes(entry.domLength || entry.dom?.length || 0)}`;
    preview.value = getPreviewText(entry);

    button.addEventListener("click", () => {
      const isOpen = item.dataset.open === "true";
      document.querySelectorAll('.history-card[data-open="true"]').forEach((openItem) => {
        if (openItem !== item) {
          openItem.dataset.open = "false";
        }
      });

      item.dataset.open = isOpen ? "false" : "true";

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

async function sendToFrontend(prompt, history) {
  const payload = {
    type: "SYNAPSE_INGEST",
    prompt,
    timestamp: Date.now(),
    pages: history.map((entry) => ({
      id: entry.id,
      url: entry.url,
      title: getDisplayTitle(entry),
      timestamp: entry.timestamp,
      domLength: entry.domLength || entry.dom?.length || 0,
      readableLength: entry.readableLength || 0,
      extractor: entry.readable?.extractor || "none",
      excerpt: entry.readable?.excerpt || "",
      byline: entry.readable?.byline || "",
      siteName: entry.readable?.siteName || "",
      lang: entry.readable?.lang || "",
      textContent: entry.readable?.textContent || "",
      readableContent: entry.readable?.content || "",
      dom: entry.dom || ""
    }))
  };

  await chrome.storage.local.set({ synapse_pending_payload: payload });

  try {
    const [existingTab] = await chrome.tabs.query({ url: `${FRONTEND_URL}/*` });

    if (existingTab) {
      await chrome.tabs.update(existingTab.id, { active: true });
      await chrome.tabs.sendMessage(existingTab.id, payload);
    } else {
      await chrome.tabs.create({ url: `${FRONTEND_URL}?synapse_ingest=1` });
    }
  } catch (error) {
    console.warn("Could not open frontend tab, payload saved to storage:", error);
  }

  return payload;
}

promptForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  const prompt = promptInput.value.trim();

  if (!prompt) {
    return;
  }

  try {
    const response = await sendMessage({ type: "GET_HISTORY" });
    const history = response.history || [];

    if (history.length === 0) {
      showToast("No snapshots to send - browse some pages first");
      return;
    }

    const payload = await sendToFrontend(prompt, history);
    showToast(`Sent ${payload.pages.length} pages to Synapse`);
    promptInput.value = "";
  } catch (error) {
    showToast(`Failed to send: ${error.message}`);
    console.error(error);
  }
});

document.querySelectorAll(".hint-chip").forEach((chip) => {
  chip.addEventListener("click", () => {
    promptInput.value = chip.textContent;
    promptInput.focus();
  });
});

clearButton.addEventListener("click", async () => {
  clearButton.disabled = true;

  try {
    await sendMessage({ type: "CLEAR_HISTORY" });
    render([]);
  } catch (error) {
    summary.textContent = error.message;
  } finally {
    clearButton.disabled = false;
  }
});

async function loadHistory() {
  try {
    const response = await sendMessage({ type: "GET_HISTORY" });
    render(response.history || []);
  } catch (error) {
    summary.textContent = error.message;
  }
}

loadHistory();
