import { chromium } from "playwright";

const browser = await chromium.launch({ headless: true });
const page = await browser.newPage({ viewport: { width: 1440, height: 1000 } });
await page.goto("http://localhost:3000", { waitUntil: "networkidle" });
await page.waitForFunction(() => {
  const text = document.body.textContent || "";
  return text.includes("nodes: 10") || text.includes("nodes: 5");
});
await page.waitForSelector("[data-testid^='research-node-']");

const initialNodes = await page.locator("[data-testid^='research-node-']").count();
await page.screenshot({ path: ".codex/tabgraph-galaxy.png", fullPage: false });

await page.getByRole("button", { name: "Compare" }).click();
await page.waitForFunction(() => document.body.textContent?.includes("mode: grid"));
await page.screenshot({ path: ".codex/tabgraph-grid.png", fullPage: false });

await page.getByRole("button", { name: "Hide AI Found" }).click();
await page.waitForFunction(() => document.body.textContent?.includes("ai found: 0"));
await page.screenshot({ path: ".codex/tabgraph-seed-only.png", fullPage: false });
await page.getByRole("button", { name: "Show AI Found" }).click();
await page.waitForFunction(() => document.body.textContent?.includes("ai found: 5"));

const selectedTarget = page.locator("[data-testid='research-node-seed-acer-nitro-v16']");
await selectedTarget.click();
await page.waitForFunction(() => document.body.textContent?.includes("selected: seed-acer-nitro-v16"));

console.log(JSON.stringify({ initialNodes, mode: "grid", selected: "seed-acer-nitro-v16" }, null, 2));
await browser.close();
