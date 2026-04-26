(function () {
  let lastSnapshotKey = "";
  let snapshotTimer = 0;

  function captureDom() {
    return document.documentElement ? document.documentElement.outerHTML : "";
  }

  function normalizeWhitespace(value) {
    return typeof value === "string" ? value.replace(/\s+/g, " ").trim() : "";
  }

  function textFromSelectors(selectors, root = document) {
    for (const selector of selectors) {
      const element = root.querySelector(selector);
      if (!isVisible(element)) {
        continue;
      }
      const text = normalizeWhitespace(element.textContent || element.getAttribute?.("aria-label") || "");
      if (text) {
        return text;
      }
    }
    return "";
  }

  function collectTexts(selector, root = document, maxCount = 8, minLength = 8) {
    const values = [];
    const seen = new Set();
    root.querySelectorAll(selector).forEach((element) => {
      if (values.length >= maxCount || !isVisible(element)) {
        return;
      }
      if (element.closest("nav, header, footer, aside, form")) {
        return;
      }
      const text = normalizeWhitespace(element.textContent || element.getAttribute?.("aria-label") || "");
      if (!text || text.length < minLength || seen.has(text)) {
        return;
      }
      seen.add(text);
      values.push(text);
    });
    return values;
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
      "[class*='review' i]",
      "[id*='review' i]",
      "[data-hook*='review' i]",
      "[aria-label*='star' i]",
      "[aria-label*='rating' i]",
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

      if (element.closest("#sp_detail, #sp_detail2, [id*='sims' i], [id*='sponsored' i], [data-component-type='sp-sponsored-result']")) {
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

  function captureAmazonProductReadable() {
    if (!/amazon\./i.test(location.hostname)) {
      return null;
    }

    const title = textFromSelectors(
      [
        "#productTitle",
        "#title",
        "[data-feature-name='title'] h1",
        "#ebooksProductTitle",
      ],
      document,
    ) || normalizeWhitespace(document.title).replace(/^Amazon\.com:\s*/i, "").replace(/\s+:\s+[^:]+$/, "");

    if (!title) {
      return null;
    }

    const lines = [`Title: ${title}`];
    const price = textFromSelectors(
      [
        "#corePrice_feature_div .a-offscreen",
        "#corePriceDisplay_desktop_feature_div .a-offscreen",
        "#apex_desktop .a-offscreen",
        "#priceblock_ourprice",
        "#priceblock_dealprice",
        "#price_inside_buybox",
        ".priceToPay .a-offscreen",
      ],
      document,
    );
    if (price) {
      lines.push(`Price: ${price}`);
    }

    const rating =
      normalizeWhitespace(document.querySelector("#acrPopover")?.getAttribute("title") || "") ||
      textFromSelectors(
        [
          "[data-hook='rating-out-of-text']",
          "#averageCustomerReviews .a-icon-alt",
          "[data-hook='average-star-rating']",
        ],
        document,
      );
    if (rating) {
      lines.push(`Star rating: ${rating}`);
    }

    const reviewCount = textFromSelectors(
      ["#acrCustomerReviewText", "#acrCustomerReviewLink", "[data-hook='total-review-count']"],
      document,
    );
    if (reviewCount) {
      lines.push(`Review count: ${reviewCount}`);
    }

    const bullets = collectTexts(
      "#feature-bullets li, #productFactsDesktopExpander li, #detailBullets_feature_div li",
      document,
      8,
      12,
    );
    if (bullets.length) {
      lines.push(`About this item: ${bullets.join(" | ")}`);
    }

    const reviewSummaryContainer =
      document.querySelector("#product-summary") ||
      document.querySelector("[data-hook='cr-insights-widget-summary']") ||
      document.querySelector("#averageCustomerReviews_feature_div") ||
      document.querySelector("#reviewsMedley");
    const reviewSummaryBlocks = reviewSummaryContainer
      ? collectTexts("p, span, div, li", reviewSummaryContainer, 8, 24)
      : [];
    const customerSayLine = reviewSummaryBlocks.find((text) => /^customers say/i.test(text));
    if (customerSayLine) {
      lines.push(customerSayLine.includes(":") ? customerSayLine : `Customers say: ${customerSayLine}`);
    }

    const aspectLines = reviewSummaryBlocks.filter((text) =>
      /(customers mention|positive|negative|reliability|noise level|cooling performance|value for money)/i.test(text),
    );
    if (aspectLines.length) {
      lines.push(`Review highlights: ${aspectLines.slice(0, 6).join(" | ")}`);
    }

    const topReviews = [];
    document.querySelectorAll("[data-hook='review']").forEach((review) => {
      if (topReviews.length >= 3 || !isVisible(review)) {
        return;
      }
      const titleText = normalizeWhitespace(
        review.querySelector("[data-hook='review-title'], .review-title")?.textContent || "",
      );
      const bodyText = normalizeWhitespace(
        review.querySelector("[data-hook='review-body'], .review-text-content")?.textContent || "",
      );
      const combined = [titleText, bodyText].filter(Boolean).join(" - ");
      if (combined && combined.length >= 40) {
        topReviews.push(combined);
      }
    });
    topReviews.forEach((reviewText, index) => {
      lines.push(`Top review ${index + 1}: ${reviewText}`);
    });

    const textContent = normalizeWhitespace(lines.join("\n\n"));
    if (!textContent) {
      return null;
    }

    return {
      extractor: "amazon-product",
      title,
      byline: "",
      excerpt: lines.slice(0, 3).join(" "),
      siteName: "Amazon",
      lang: document.documentElement?.lang || "",
      length: textContent.length,
      content: lines.map((line) => `<p>${escapeHtml(line)}</p>`).join(""),
      textContent,
    };
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

    document
      .querySelectorAll("[class*='review' i], [id*='review' i], [data-hook*='review' i], [aria-label*='rating' i]")
      .forEach((element) => {
        if (!isVisible(element)) {
          return;
        }
        const text = normalizeWhitespace(element.textContent || element.getAttribute("aria-label") || "");
        if (text && text.length >= 8 && !metadata.includes(text)) {
          metadata.push(`Review signal: ${text}`);
        }
      });

    return metadata.slice(0, 18);
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
    const amazonReadable = captureAmazonProductReadable();
    if (amazonReadable) {
      return amazonReadable;
    }

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
