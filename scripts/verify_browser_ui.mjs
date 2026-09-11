/** Browser-level layout and keyboard checks against an isolated, running sidecar.
 * Usage: NODE_PATH=/path/to/node_modules node scripts/verify_browser_ui.mjs URL OUTPUT
 * The URL must use the development-only loopback sidecar connection hook.
 */
import assert from "node:assert/strict";
import { createRequire } from "node:module";
import fs from "node:fs/promises";
import path from "node:path";
const require = createRequire(import.meta.url);
const { chromium } = require("playwright");
const [url, output, populated] = process.argv.slice(2);
assert(url && output, "Provide an isolated UI URL and an output directory");
assert.equal(new URL(url).hostname, "127.0.0.1", "Only an isolated local UI may be tested");
await fs.mkdir(output, { recursive: true });
const browser = await chromium.launch({ headless: true });
const results = [], errors = [];
const pages = [["快速开始", "quick"], ["项目概览", "overview"], ["源码材料", "source"],
  ["说明书", "manual"], ["界面截图", "screenshots"], ["图表资产", "diagrams"],
  ["我的资产", "assets"], ["运行日志", "logs"], ["设置", "settings"]];
try {
  for (const width of [1440, 1080, 900]) {
    const page = await browser.newPage({ viewport: { width, height: 900 } });
    page.on("pageerror", (error) => errors.push(error.message));
    await page.goto(url);
    await page.getByText("本地服务已连接", { exact: false }).waitFor();
    assert.equal(await page.locator("h1").innerText(), "快速开始");
    for (const [name, key] of pages) {
      const navigation = page.locator(".sidebar");
      if (["overview", "source", "manual", "screenshots", "diagrams"].includes(key)) {
        const details = navigation.locator(".workspace-navigation");
        if (!(await details.evaluate((element) => element.open))) await details.locator("summary").click();
      }
      await navigation.getByRole("button", { name, exact: true }).click();
      await page.locator("main h1").waitFor();
      if (populated === "--populated") {
        const ready = { quick: ".quick-run-summary", source: ".artifact-success",
          manual: ".manual-result", screenshots: ".screenshot-evidence-content img",
          diagrams: ".diagram-editor-toolbar", assets: ".asset-library-list article",
          settings: ".provider-model" }[key];
        if (ready) await page.locator(ready).first().waitFor();
        if (key === "diagrams") await page.getByLabel("AI 图表模型").locator("option").first().waitFor({ state: "attached" });
      }
      await page.waitForTimeout(300);
      await page.waitForLoadState("networkidle");
      const layout = await page.locator("main").evaluate((main) => {
        const tiny = [];
        for (const element of main.querySelectorAll("*")) {
          if (!element.getClientRects().length || !element.checkVisibility()) continue;
          if ([...element.childNodes].some((node) => node.nodeType === 3 && node.textContent.trim()) &&
              parseFloat(getComputedStyle(element).fontSize) < 12)
            tiny.push({ tag: element.tagName, size: getComputedStyle(element).fontSize });
        }
        return { scrollWidth: main.scrollWidth, clientWidth: main.clientWidth, tiny };
      });
      results.push({ width, page: key, ...layout });
      await page.screenshot({ path: path.join(output, `${key}-${width}.png`) });
      assert(layout.scrollWidth <= layout.clientWidth + 2, `${key} overflows at ${width}px`);
      assert.equal(layout.tiny.length, 0, `${key} contains unreadable text at ${width}px`);
    }
    await page.close();
  }
  const page = await browser.newPage({ viewport: { width: 1440, height: 1000 } });
  await page.goto(url);
  await page.getByText("本地服务已连接", { exact: false }).waitFor();
  await page.locator(".workspace-navigation summary").click();
  await page.locator(".sidebar").getByRole("button", { name: "源码材料", exact: true }).click();
  const open = page.getByRole("button", { name: "程序内查看", exact: true });
  await open.waitFor({ timeout: 5000 }).catch(() => {});
  if (await open.isVisible() && await open.isEnabled()) {
    await open.click();
    const modal = page.getByRole("dialog", { name: "源代码文档预览" });
    await modal.waitFor();
    await modal.locator("img").waitFor();
    await modal.locator("img").evaluate((image) => image.decode());
    assert(await modal.locator("img").evaluate((image) => image.naturalWidth > 0));
    assert(await page.locator("#root").evaluate((root) => root.inert));
    for (let i = 0; i < 25; i++) {
      await page.keyboard.press("Tab");
      assert(await modal.evaluate((dialog) => dialog.contains(document.activeElement)), "Focus escaped modal");
    }
    await page.screenshot({ path: path.join(output, "source-modal.png") });
    await page.keyboard.press("Escape");
    assert.equal(await page.getByRole("dialog").count(), 0);
    assert.equal(await page.evaluate(() => document.activeElement.textContent), "程序内查看");
    assert.equal(await page.locator("#root").evaluate((root) => root.inert), false);
    results.push({ source_preview_keyboard: "passed", actual_image: true });
    await page.locator(".sidebar").getByRole("button", { name: "我的资产", exact: true }).click();
    await page.getByRole("button", { name: "程序内查看", exact: true }).first().click();
    await modal.waitFor();
    await page.keyboard.press("Escape");
    await page.locator(".sidebar").getByRole("button", { name: "我的资产", exact: true }).click();
    await page.getByRole("button", { name: "进入项目", exact: true }).first().click();
    await page.locator("main h1").filter({ hasText: "源码材料" }).waitFor();
    await page.waitForTimeout(500);
    assert.equal(await page.getByRole("dialog").count(), 0, "Consumed asset preview must not reopen on normal navigation");
    results.push({ source_preview_one_shot: "passed" });
  } else results.push({ source_preview_keyboard: "not_exercised_no_source_document" });
  // Only health responses are replaced; no project mutation or service process is touched.
  await page.route("**/api/v1/health", (route) => route.fulfill({ status: 503, body: "offline fixture" }));
  await page.getByText("本地服务连接已中断", { exact: false }).waitFor({ timeout: 23000 });
  await page.unroute("**/api/v1/health");
  await page.getByRole("button", { name: "重新连接本地服务", exact: true }).click();
  await page.getByText("本地服务已连接", { exact: false }).waitFor();
  results.push({ disconnected_reconnect: "passed" });
  assert.deepEqual(errors, [], "Browser runtime errors");
} finally {
  await fs.writeFile(path.join(output, "result.json"), JSON.stringify({ results, errors }, null, 2));
  await browser.close();
}
console.log(JSON.stringify({ checks: results.length, errors: errors.length }));
