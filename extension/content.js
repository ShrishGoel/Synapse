(function () {
  let lastSnapshotKey = "";
  let snapshotTimer = 0;

  function captureDom() {
    return document.documentElement ? document.documentElement.outerHTML : "";
  }

  function sendSnapshot(reason) {
    const dom = captureDom();
    const key = `${location.href}|${dom.length}`;

    if (key === lastSnapshotKey) {
      return;
    }

    lastSnapshotKey = key;

    chrome.runtime.sendMessage({
      type: "DOM_SNAPSHOT",
      reason,
      url: location.href,
      title: document.title,
      dom
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
    characterData: true
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
