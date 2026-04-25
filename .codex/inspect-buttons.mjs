import { chromium } from "playwright";
const browser = await chromium.launch({ headless: true });
const page = await browser.newPage({ viewport: { width: 1440, height: 1000 } });
await page.goto("http://localhost:3000", { waitUntil: "networkidle" });
await page.getByRole("button", { name: "Compare" }).click();
await page.waitForFunction(() => document.body.textContent?.includes("mode: grid"));
const buttons = await page.locator("button").evaluateAll(btns => btns.map(b => ({ text: b.textContent, className: b.className, pressed: b.getAttribute("aria-pressed") })));
console.log(JSON.stringify(buttons, null, 2));
await browser.close();
