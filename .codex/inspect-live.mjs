import { chromium } from "playwright";

const browser = await chromium.launch({ headless: true });
const page = await browser.newPage({ viewport: { width: 1440, height: 1000 } });
page.on("console", (message) => console.log("console:", message.type(), message.text()));
page.on("pageerror", (error) => console.log("pageerror:", error.message));
await page.goto("http://localhost:3000", { waitUntil: "networkidle" });
await page.waitForTimeout(3000);
console.log(await page.locator("body").innerText());
await browser.close();
