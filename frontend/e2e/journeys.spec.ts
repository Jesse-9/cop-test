import { expect, test } from "@playwright/test";
import { installMocks } from "./mock";

test.beforeEach(async ({ page }) => {
  await installMocks(page);
});

test("手工订单：添加节点、连接、预览并保存", async ({ page }) => {
  await page.goto("/orders/new");
  await expect(page.getByRole("heading", { name: "新建订单" })).toBeVisible();

  await page.getByRole("button", { name: "添加模板 a" }).click();
  await page.getByRole("button", { name: "添加模板 c" }).click();
  await page.getByRole("button", { name: "返回订单设置" }).click();

  await page.locator("#new-input-zone").fill("line_s1a");
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
