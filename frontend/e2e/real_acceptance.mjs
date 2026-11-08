/**
 * 阶段 6 真实 UI 验收脚本（手动运行，不是默认测试）.
 *
 * 前置：
 *   1. CoppeliaSim 4.10 + ZMQ 23000；
 *   2. `flexible-factory api serve --host 127.0.0.1 --port 8080 ...`；
 *   3. `cd frontend && npm run dev`；
 *   4. `npm run e2e:install` 安装 Chromium。
 *
 * 用法：
 *   node e2e/real_acceptance.mjs manual
 *   node e2e/real_acceptance.mjs planner
 *   RUN_ID=<run_id> node e2e/real_acceptance.mjs verify
 *
 * 脚本通过真实后端创建/保存订单并从 UI 启动；不使用 route mock。运行可能
 * 持续数十分钟，默认等待 60 分钟。verify 只检查已结束运行在刷新前后一致。
 */
import { chromium } from "playwright";

const BASE = process.env.E2E_BASE_URL || "http://127.0.0.1:5173";
const ARTIFACT_ID = process.env.ACCEPT_ARTIFACT_ID;
const MODE = process.argv[2] || "manual";

if (!ARTIFACT_ID) {
  console.error("必须设置 ACCEPT_ARTIFACT_ID");
  process.exit(2);
}

async function waitForPreview(page) {
  await page.getByRole("button", { name: "服务端预览" }).click();
  await page
    .getByText("预览通过")
    .first()
    .waitFor({ timeout: 60_000 });
}

async function waitNodeRows(page, expected) {
  if (expected === null || expected === undefined) return;
  await page.waitForFunction(
    (count) =>
      document.querySelectorAll(".data-table tbody tr").length === count,
    expected,
    { timeout: 60_000 },
  );
}

async function startRun(page) {
  const expectedNodes = process.env.EXPECT_NODES
    ? Number(process.env.EXPECT_NODES)
    : null;
  await page.locator("#start-artifact").waitFor({ timeout: 60_000 });
  await page.locator("#start-artifact").fill(ARTIFACT_ID);
  await page.getByRole("button", { name: "确认启动" }).click();
  await page.waitForURL(/\/runs\//, { timeout: 60_000 });
  const runUrl = page.url();
  await page.waitForFunction(
    () => {
      const badge = document.querySelector(".run-title .badge");
      if (!badge) return false;
      return ["成功", "部分失败", "失败", "全局中止"].includes(
        (badge.textContent || "").trim(),
      );
    },
    null,
    { timeout: 60 * 60_000 },
  );
  await waitNodeRows(page, expectedNodes);
  const status = (await page.locator(".run-title .badge").innerText()).trim();
  await page.reload();
  await page.locator(".run-title .badge").waitFor({ timeout: 60_000 });
  await waitNodeRows(page, expectedNodes);
  const reloaded = (await page.locator(".run-title .badge").innerText()).trim();
  if (reloaded !== status) {
    throw new Error(`刷新后状态不一致: ${status} != ${reloaded}`);
  }
  const nodeRows = await page.locator(".data-table tbody tr").count();
  console.log(`RUN_URL=${runUrl}`);
  console.log(`RUN_STATUS=${status}`);
  console.log(`RUN_RELOAD_STATUS=${reloaded}`);
  console.log(`RUN_NODE_ROWS=${nodeRows}`);
  console.log(`RUN_PAGE_TEXT=${(await page.locator("body").innerText()).slice(0, 1200)}`);
}

async function verifyRun(page) {
  const runId = process.env.RUN_ID;
  if (!runId) throw new Error("verify 模式需要 RUN_ID");
  const expectedNodes = process.env.EXPECT_NODES
    ? Number(process.env.EXPECT_NODES)
    : null;
  const runUrl = `${BASE}/runs/${encodeURIComponent(runId)}`;
  await page.goto(runUrl);
  await page.waitForFunction(
    () => {
      const badge = document.querySelector(".run-title .badge");
      if (!badge) return false;
      return ["成功", "部分失败", "失败", "全局中止"].includes(
        (badge.textContent || "").trim(),
      );
    },
    null,
    { timeout: 60_000 },
  );
  await waitNodeRows(page, expectedNodes);
  const before = (await page.locator(".run-title .badge").innerText()).trim();
  await page.reload();
  await page.locator(".run-title .badge").waitFor({ timeout: 60_000 });
  await waitNodeRows(page, expectedNodes);
  const after = (await page.locator(".run-title .badge").innerText()).trim();
  if (before !== after) {
    throw new Error(`刷新后状态不一致: ${before} != ${after}`);
  }
  console.log(`RUN_URL=${runUrl}`);
  console.log(`RUN_STATUS=${after}`);
  console.log(`RUN_NODE_ROWS=${await page.locator(".data-table tbody tr").count()}`);
  console.log(`RUN_PAGE_TEXT=${(await page.locator("body").innerText()).slice(0, 1200)}`);
}

async function connect(page, source, target, port) {
  await page.locator("#connect-source").selectOption(source);
  await page.locator("#connect-target").selectOption(target);
  await page.locator("#connect-port").selectOption(port);
  await page.getByRole("button", { name: "添加连线" }).click();
}

async function createManualOrder(page) {
  const orderId = `accept-ui-manual-${Date.now()}`;
  await page.goto(`${BASE}/orders/new`);
  await page.getByRole("heading", { name: "新建订单" }).waitFor();
  await page.locator("#order-id").fill(orderId);
  for (const template of [
    "housing_direct_A",
    "housing_finish",
    "housing_to_E_buffer",
    "bracket_process",
    "transport_DE",
    "bracket_to_E",
    "kit_join",
  ]) {
    await page.getByRole("button", { name: `添加模板 ${template}` }).click();
  }
  await page.getByRole("button", { name: "返回订单设置" }).click();
  await page.locator("#new-input-type").selectOption("RAW_A");
  await page.locator("#new-input-zone").fill("line_s1a");
  await page.getByRole("button", { name: "添加入口投料" }).click();
  await page.locator("#new-input-type").selectOption("RAW_D");
  await page.locator("#new-input-zone").fill("line_s2d");
  await page.getByRole("button", { name: "添加入口投料" }).click();
  await connect(page, "housing_direct_A-01", "housing_finish-01", "input-0");
  await connect(page, "housing_finish-01", "housing_to_E_buffer-01",
    "input-0");
  await connect(page, "bracket_process-01", "transport_DE-01", "input-0");
  await connect(page, "transport_DE-01", "bracket_to_E-01", "input-0");
  await connect(page, "housing_to_E_buffer-01", "kit_join-01",
    "housing-input");
  await connect(page, "bracket_to_E-01", "kit_join-01", "bracket-input");
  await waitForPreview(page);
  await page.getByRole("button", { name: "保存订单" }).click();
  await page.waitForURL(new RegExp(`/orders/${orderId}$`), {
    timeout: 60_000,
  });
  console.log(`ORDER_ID=${orderId}`);
  if (process.env.NO_START === "1") return;
  await startRun(page);
}

async function createPlannerOrder(page) {
  await page.goto(`${BASE}/planner`);
  await page.getByRole("button", { name: "生成候选" }).click();
  await page.getByText("找到可行候选").waitFor({ timeout: 60_000 });
  await page.getByRole("button", { name: "保存草稿并编辑" }).click();
  await page.waitForURL(/\/orders\/[^/]+\/edit$/, { timeout: 60_000 });
  const match = page.url().match(/\/orders\/([^/]+)\/edit$/);
  const orderId = match ? decodeURIComponent(match[1]) : "";
  console.log(`ORDER_ID=${orderId}`);
  if (process.env.NO_START === "1") return;
  await page.goto(`${BASE}/orders/${encodeURIComponent(orderId)}`);
  await startRun(page);
}

const browser = await chromium.launch();
const page = await browser.newPage();
page.setDefaultTimeout(60_000);
page.on("pageerror", (error) => console.error("PAGEERROR", error.message));
try {
  if (MODE === "manual") {
    await createManualOrder(page);
  } else if (MODE === "planner") {
    await createPlannerOrder(page);
  } else if (MODE === "start") {
    const orderId = process.env.ORDER_ID;
    if (!orderId) throw new Error("start 模式需要 ORDER_ID");
    await page.goto(`${BASE}/orders/${encodeURIComponent(orderId)}`);
    await startRun(page);
  } else if (MODE === "verify") {
    await verifyRun(page);
  } else {
    throw new Error(`未知模式 ${MODE}`);
  }
} finally {
  await browser.close();
}
