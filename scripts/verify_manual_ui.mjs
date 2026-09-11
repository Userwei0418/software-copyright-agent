/** Real DOM checks on a populated, isolated audit database. All writes are intercepted. */
import assert from "node:assert/strict";
import { createRequire } from "node:module";
import fs from "node:fs/promises";
import path from "node:path";
const { chromium } = createRequire(import.meta.url)("playwright");
const [url, output] = process.argv.slice(2);
assert(url && output && new URL(url).hostname === "127.0.0.1");
await fs.mkdir(output, { recursive: true });
const browser = await chromium.launch({ headless: true });
const page = await browser.newPage({ viewport: { width: 1440, height: 1000 } });
const checks = [], errors = [];
let saveAttempts = 0, releaseSave;
const saveGate = new Promise((resolve) => { releaseSave = resolve; });
page.on("pageerror", (error) => errors.push(error.message));
// Test editing without changing even the isolated backend's asset history.
await page.route("**/api/v1/**", async (route) => {
  if (["GET", "HEAD", "OPTIONS"].includes(route.request().method())) return route.continue();
  if (route.request().method() === "PUT" && /\/sections\/[^/]+$/.test(new URL(route.request().url()).pathname)) {
    saveAttempts++;
    await saveGate;
  }
  return route.fulfill({ status: 503, contentType: "application/json",
    body: JSON.stringify({ detail: "UI audit: intercepted write; no data was changed" }) });
});
try {
  await page.goto(url);
  await page.getByText("本地服务已连接", { exact: false }).waitFor();
  await page.locator(".workspace-navigation summary").click();
  await page.locator(".sidebar").getByRole("button", { name: "说明书", exact: true }).click();
  await page.getByRole("button", { name: "逐页预览", exact: true }).click();
  const preview = page.getByRole("dialog", { name: "说明书预览与质量检查" });
  await preview.locator("img").waitFor();
  await preview.locator("img").evaluate((image) => image.decode());
  assert(await preview.locator("img").evaluate((image) => image.naturalWidth > 0));
  await preview.getByLabel("说明书跳转页码").fill("7");
  await preview.locator('img[alt*="7"]').waitFor();
  await preview.locator("img").evaluate((image) => image.decode());
  await page.screenshot({ path: path.join(output, "manual-page-7.png") });
  await page.keyboard.press("Escape");
  assert.equal(await page.getByRole("dialog").count(), 0);
  checks.push("manual_actual_preview_page_7_and_escape");
  // The candidate version is editable; a final version remains immutable.
  await page.locator(".manual-versions button").filter({ has: page.locator("b", { hasText: /^v2$/ }) }).click();
  await page.getByRole("button", { name: "编辑内容", exact: true }).click();
  const editor = page.getByRole("dialog", { name: "编辑说明书正文" });
  const title = editor.getByLabel("章节标题");
  await title.fill("未保存的审计修订");
  await page.route("**/api/v1/health", (route) => route.fulfill({ status: 503, body: "offline fixture" }));
  await page.locator(".side-status").filter({ hasText: "连接已中断" }).waitFor({ timeout: 23000 });
  assert.equal(await title.inputValue(), "未保存的审计修订");
  await page.unroute("**/api/v1/health");
  await page.locator(".side-status").filter({ hasText: "本地服务已连接" }).waitFor({ timeout: 12000 });
  assert.equal(await title.inputValue(), "未保存的审计修订");
  checks.push("unsaved_chapter_survives_real_dom_offline_and_recovery");
  await editor.getByRole("button", { name: "保存本章修订", exact: true }).click();
  await page.waitForFunction(() => document.querySelector('.manual-editor input')?.disabled === true);
  assert(await editor.locator("input, textarea, select").evaluateAll((controls) =>
    controls.length > 1 && controls.every((control) => control.disabled)));
  assert(await editor.locator("nav button").evaluateAll((buttons) => buttons.every((button) => button.disabled)));
  assert(await editor.getByRole("button", { name: "关闭", exact: true }).isDisabled());
  await page.screenshot({ path: path.join(output, "manual-editor-saving.png") });
  releaseSave();
  await page.waitForFunction(() => document.querySelector('.manual-editor input')?.disabled === false);
  assert.equal(await title.inputValue(), "未保存的审计修订");
  assert.equal(saveAttempts, 1);
  checks.push("all_editor_controls_lock_and_failed_save_retains_draft_no_replay");
  assert.deepEqual(errors, []);
} finally {
  releaseSave();
  await fs.writeFile(path.join(output, "result.json"), JSON.stringify({ checks, errors, writes_reached_backend: 0 }, null, 2));
  await browser.close();
}
console.log(JSON.stringify({ checks: checks.length, errors: errors.length }));
