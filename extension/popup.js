const summary = document.querySelector("#summary");
const clearButton = document.querySelector("#clearButton");
const historyList = document.querySelector("#historyList");
const emptyState = document.querySelector("#emptyState");
const template = document.querySelector("#historyItemTemplate");

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

    title.textContent = entry.title || "Untitled page";
    url.textContent = entry.url;
    meta.textContent = `${formatTime(entry.timestamp)} • ${formatBytes(entry.domLength || entry.dom?.length || 0)}`;
    preview.value = entry.dom || "";

    button.addEventListener("click", () => {
      const isOpen = item.dataset.open === "true";
      // Close others (optional, for a cleaner accordian look)
      document.querySelectorAll('.history-card[data-open="true"]').forEach(el => {
        if (el !== item) el.dataset.open = "false";
      });
      item.dataset.open = isOpen ? "false" : "true";
      if (!isOpen) {
        item.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
      }
    });

    const copyBtn = fragment.querySelector(".copy-button");
    copyBtn.addEventListener("click", async (e) => {
      e.stopPropagation();
      try {
        await navigator.clipboard.writeText(entry.dom || "");
        const label = copyBtn.querySelector("span");
        const originalText = label.textContent;
        label.textContent = "Copied!";
        copyBtn.classList.add("copied");
        setTimeout(() => {
          label.textContent = originalText;
          copyBtn.classList.remove("copied");
        }, 2000);
      } catch (err) {
        console.error("Failed to copy", err);
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

loadHistory();
