(function () {
  let lastSnapshotKey = "";
  let snapshotTimer = 0;

  function captureDom() {
    return document.documentElement ? document.documentElement.outerHTML : "";
  }

  function normalizeWhitespace(value) {
    return typeof value === "string" ? value.replace(/\s+/g, " ").trim() : "";
  }

  function pickRootElement() {
    return (
      document.querySelector("main") ||
      document.querySelector("[role='main']") ||
      document.querySelector("article") ||
      document.body
    );
  }

  function isVisible(element) {
    if (!(element instanceof Element)) {
      return false;
    }

    if (element.hidden || element.getAttribute("aria-hidden") === "true") {
      return false;
    }

    const style = window.getComputedStyle(element);
    if (style.display === "none" || style.visibility === "hidden" || style.opacity === "0") {
      return false;
    }

    return true;
  }

  function collectTextBlocks(root) {
    const selector = [
      "h1",
      "h2",
      "h3",
      "p",
      "li",
      "blockquote",
      "figcaption",
      "td",
      "th",
      "dd",
      "dt",
      "button",
      "label",
      "summary",
    ].join(",");
    const blocks = [];
    const seen = new Set();

    root.querySelectorAll(selector).forEach((element) => {
      if (!isVisible(element)) {
        return;
      }

      if (element.closest("nav, header, footer, aside, form")) {
        return;
      }

      const text = normalizeWhitespace(element.textContent);
      if (!text || text.length < 20 || seen.has(text)) {
        return;
      }

      seen.add(text);
      blocks.push(text);
    });

    return blocks.slice(0, 120);
  }

  function collectMetadata(root) {
    const metadata = [];
    const title = normalizeWhitespace(document.title);
    const description = normalizeWhitespace(
      document.querySelector("meta[name='description']")?.content ||
        document.querySelector("meta[property='og:description']")?.content
    );

    if (title) {
      metadata.push(`Title: ${title}`);
    }

    if (description) {
      metadata.push(`Description: ${description}`);
    }

    root.querySelectorAll("h1, h2, h3").forEach((element) => {
      const text = normalizeWhitespace(element.textContent);
      if (text && !metadata.includes(text)) {
        metadata.push(text);
      }
    });

    return metadata.slice(0, 10);
  }

  function escapeHtml(value) {
    return value
      .replaceAll("&", "&amp;")
      .replaceAll("<", "&lt;")
      .replaceAll(">", "&gt;")
      .replaceAll('"', "&quot;")
      .replaceAll("'", "&#39;");
  }

  function captureReadable() {
    const root = pickRootElement();
    if (!root) {
      return null;
    }

    const blocks = collectTextBlocks(root);
    const metadata = collectMetadata(root);
    const combinedBlocks = [...metadata, ...blocks];
    const textContent = normalizeWhitespace(combinedBlocks.join("\n\n"));

    if (!textContent) {
      return null;
    }

    return {
      extractor: "generic",
      title: normalizeWhitespace(document.title),
      byline: "",
      excerpt: combinedBlocks.slice(0, 3).join(" "),
      siteName:
        normalizeWhitespace(document.querySelector("meta[property='og:site_name']")?.content) || location.hostname,
      lang: document.documentElement?.lang || "",
      length: textContent.length,
      content: combinedBlocks.map((block) => `<p>${escapeHtml(block)}</p>`).join(""),
      textContent,
    };
  }

  function sendSnapshot(reason) {
    const dom = captureDom();
    const readable = captureReadable();
    const key = `${location.href}|${dom.length}|${readable?.extractor || "none"}|${readable?.length || 0}`;

    if (key === lastSnapshotKey) {
      return;
    }

    lastSnapshotKey = key;
    chrome.runtime.sendMessage({
      type: "DOM_SNAPSHOT",
      reason,
      url: location.href,
      title: document.title,
      dom,
      readable,
    });
  }

  function scheduleSnapshot(reason) {
    window.clearTimeout(snapshotTimer);
    snapshotTimer = window.setTimeout(() => sendSnapshot(reason), 500);
  }

  scheduleSnapshot("initial-load");

  const observer = new MutationObserver(() => scheduleSnapshot("dom-change"));
  observer.observe(document.documentElement, {
    childList: true,
    subtree: true,
    attributes: true,
    characterData: true,
  });

  let previousUrl = location.href;
  window.setInterval(() => {
    if (location.href !== previousUrl) {
      previousUrl = location.href;
      lastSnapshotKey = "";
      scheduleSnapshot("url-change");
    }
  }, 1000);

  window.addEventListener("beforeunload", () => sendSnapshot("before-unload"));
})();
