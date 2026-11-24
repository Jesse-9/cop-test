import { expect, test } from "@playwright/test";
import { installMocks } from "./mock";

test.beforeEach(async ({ page }) => {
  await installMocks(page);
});

test("订单列表：加载真实摘要响应（不含 versions）", async ({ page }) => {
  await page.goto("/orders");
  await expect(
    page.getByRole("heading", { name: "订单", exact: true }),
  ).toBeVisible();
  await expect(page.getByText("order-1").first()).toBeVisible();
  await expect(page.getByText("planner-order").first()).toBeVisible();
  await expect(page.getByText("2 / 2 个订单").first()).toBeVisible();
});

test("手工订单：添加节点、连接、预览并保存", async ({ page }) => {
  await page.goto("/orders/new");
  await expect(page.getByRole("heading", { name: "新建订单" })).toBeVisible();

  await page.getByRole("button", { name: "添加模板 a" }).click();
  await page.getByRole("button", { name: "添加模板 c" }).click();
  await page.getByRole("button", { name: "返回订单设置" }).click();

  await page.locator("#new-input-zone").selectOption("line_s1a");
  await page.getByRole("button", { name: "添加入口投料" }).click();

  await page.locator("#connect-source").selectOption({ index: 1 });
  await page.locator("#connect-target").selectOption({ index: 2 });
  await page.locator("#connect-port").selectOption("input-0");
  await page.getByRole("button", { name: "添加连线" }).click();
  await expect(page.getByText("edge #0", { exact: false }).first()).toBeVisible();

  await page.getByRole("button", { name: "服务端预览" }).click();
  await expect(page.getByText("预览通过").first()).toBeVisible();

  const save = page.getByRole("button", { name: "保存订单" });
  await expect(save).toBeEnabled();
  await save.click();
  await expect(page).toHaveURL(/\/orders\/order-draft$/);
});

test("planner：生成候选、保存草稿并进入编辑器", async ({ page }) => {
  await page.goto("/planner");
  await page.getByRole("button", { name: "生成候选" }).click();
  await expect(page.getByText("找到可行候选")).toBeVisible();
  await expect(page.locator(".planner-selected")).toContainText("route_a");

  await page.getByRole("button", { name: "保存草稿并编辑" }).click();
  await expect(page).toHaveURL(/\/orders\/planner-order\/edit$/);
  await expect(
    page.getByRole("heading", { name: "编辑 planner-order" }),
  ).toBeVisible();
  await expect(page.getByText("已保存").first()).toBeVisible();
});

test("订单方案目录：查看机械臂/运输并一键创建订单", async ({ page }) => {
  await page.goto("/orders");
  await expect(page.getByText("订单方案目录")).toBeVisible();
  await expect(page.getByText("壳体直达 + 支架齐套 → FINAL")).toBeVisible();
  await expect(page.getByText("A 臂上料 → SEMI_AC")).toBeVisible();

  await page.getByRole("button", { name: "使用此方案创建订单" }).first().click();
  await page.locator("#variant-order-id").fill("variant-order-1");
  await page.getByRole("button", { name: "创建并打开订单" }).click();
  await expect(page).toHaveURL(/\/orders\/variant-order-1$/);
});

test("订单启动：路径复用模式随请求发送", async ({ page }) => {
  await page.goto("/orders/order-1");
  await expect(page.getByText("预览通过").first()).toBeVisible();
  await expect(page.locator("#start-path-reuse")).toHaveValue(
    "CACHE_THEN_PLAN",
  );
  await page.locator("#start-path-reuse").selectOption("CACHE_ONLY");
  const startRequest = page.waitForRequest(
    (request) =>
      request.method() === "POST" &&
      /\/api\/orders\/[^/]+\/versions\/\d+\/start$/.test(request.url()),
  );
  await page.getByRole("button", { name: "确认启动" }).click();
  const request = await startRequest;
  expect(request.postDataJSON().path_reuse).toBe("CACHE_ONLY");
  await expect(page).toHaveURL(/\/runs\/run-1$/);
});

test("运行视图：局部失败与被阻断分支、全局中止", async ({ page }) => {
  await page.goto("/runs/run-1");
  await expect(page.getByRole("heading", { name: /运行 run-1/ })).toBeVisible();
  await expect(page.getByText("部分失败").first()).toBeVisible();
  await expect(page.getByText("已阻断").first()).toBeVisible();
  await expect(page.getByText("NODE_PRECHECK_REJECTED").first()).toBeVisible();
  await page.getByRole("button", { name: /a-01/ }).first().click();
  await expect(page.getByText("失败原因", { exact: true })).toBeVisible();

  await page.goto("/runs/run-2");
  await expect(page.getByText("全局中止").first()).toBeVisible();
  await expect(page.getByText("安全锁存").first()).toBeVisible();
  await expect(page.getByText("已阻断").first()).toBeVisible();
});
