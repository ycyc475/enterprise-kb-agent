import { test, expect } from "./fixtures";
import type { Page } from "@playwright/test";
import { readFileSync } from "node:fs";
const credentials = JSON.parse(
  readFileSync(
    new URL("../../data/runtime/demo-credentials.json", import.meta.url),
    "utf8",
  ),
) as Record<string, string>;
async function login(page: Page, username = "employee_li") {
  await page.goto("/login");
  await page.getByLabel("账号", { exact: true }).fill(username);
  await page.getByLabel("密码", { exact: true }).fill(credentials[username]!);
  const response = page.waitForResponse(
    (r) =>
      r.url().endsWith("/api/v1/auth/login") && r.request().method() === "POST",
  );
  await page.getByRole("button", { name: "登录", exact: true }).click();
  const identity = (await (await response).json()).data;
  await expect(page.getByRole("textbox", { name: "输入问题" })).toBeVisible();
  return { Authorization: "Bearer " + identity.access_token };
}

test("M4自然语言提取先确认，再由Agent调用程序计算840元并查看来源", async ({
  page,
  request,
}) => {
  test.setTimeout(120000);
  const headers = await login(page);
  let id = "";
  try {
    await page
      .getByRole("textbox", { name: "输入问题" })
      .fill("我去北京出差3天，住宿2晚。请帮我估算住宿和补贴预算。");
    await page.getByRole("button", { name: "发送 ↑", exact: true }).click();
    await expect(page).toHaveURL(/\/chat\/[0-9a-f-]+/);
    id = page.url().split("/").pop()!;
    const proposal = page.getByTestId("trip-proposal").last();
    await expect(proposal).toContainText("北京", { timeout: 45000 });
    await expect(proposal).toContainText("3天");
    await expect(proposal).toContainText("2晚");
    await expect(page.getByTestId("budget-result")).toHaveCount(0);
    const before = (
      await (
        await request.get(`/api/v1/conversations/${id}`, { headers })
      ).json()
    ).data;
    expect(before.trip_context).toEqual({});
    await page.screenshot({
      path: "../.m5/regression-m4-proposal-desktop.png",
      fullPage: true,
    });
    await proposal
      .getByRole("button", { name: "确认条件并计算", exact: true })
      .click();
    const budget = page.getByTestId("budget-result").last();
    await expect(budget).toContainText("¥840.00", { timeout: 45000 });
    await expect(budget).toContainText("¥600.00");
    await expect(budget).toContainText("¥240.00");
    await expect(budget).toContainText("S1");
    await expect(budget).toContainText("虚构演示制度-住宿标准");
    await page.screenshot({
      path: "../.m5/regression-m4-budget-desktop.png",
      fullPage: true,
    });
    await page.reload();
    await expect(page.getByTestId("budget-result")).toContainText("¥840.00");
    const answer = page.getByTestId("assistant-message").last();
    await answer.locator(".answer-citations button").first().click();
    await expect(page.locator(".citation-detail blockquote")).not.toBeEmpty();
    const download = page.waitForEvent("download");
    await page.getByRole("button", { name: "下载原文件", exact: true }).click();
    expect((await download).suggestedFilename()).toContain("m4-");
    await page.getByRole("button", { name: "关闭此对话框" }).click();
    await expect(page.locator(".el-drawer")).toBeHidden();
    await page.setViewportSize({ width: 390, height: 844 });
    await page.screenshot({
      path: "../.m5/regression-m4-budget-mobile.png",
      fullPage: true,
    });
    expect(
      await page.evaluate(
        () => document.documentElement.scrollWidth <= window.innerWidth,
      ),
    ).toBeTruthy();
  } finally {
    if (id) await request.delete(`/api/v1/conversations/${id}`, { headers });
  }
});

test("M4条件卡直接计算与未知城市不生成完整合计", async ({ page, request }) => {
  const headers = await login(page);
  let id = "";
  try {
    await page.getByLabel("目的地", { exact: true }).fill("广州");
    await page.getByRole("spinbutton", { name: "出差天数" }).fill("3");
    await page.getByRole("spinbutton", { name: "住宿晚数" }).fill("2");
    await page
      .getByRole("button", { name: "确认并计算预算", exact: true })
      .click();
    await expect(page).toHaveURL(/\/chat\/[0-9a-f-]+/);
    id = page.url().split("/").pop()!;
    await expect(page.getByTestId("budget-result")).toContainText("暂不可用");
    await expect(page.getByTestId("budget-result")).toContainText("¥240.00");
    await page.getByLabel("目的地", { exact: true }).fill("北京");
    await page
      .getByRole("button", { name: "确认并计算预算", exact: true })
      .click();
    await expect(page.getByTestId("budget-result").last()).toContainText(
      "¥840.00",
    );
  } finally {
    if (id) await request.delete(`/api/v1/conversations/${id}`, { headers });
  }
});

test("M4管理员核对原文、暂停与重新启用规则", async ({ page, request }) => {
  const headers = await login(page, "admin");
  const list = (
    await (await request.get("/api/v1/admin/fee-rules", { headers })).json()
  ).data.items;
  const original = list.find(
    (r: { name: string }) => r.name === "演示-S1-北京上海住宿",
  );
  try {
    await page.getByRole("link", { name: "费用规则", exact: true }).click();
    await expect(
      page.getByRole("heading", { name: "费用规则", exact: true }),
    ).toBeVisible();
    const row = page
      .getByRole("row")
      .filter({ hasText: "演示-S1-北京上海住宿" });
    await expect(row).toContainText("300.00");
    await row.getByRole("button", { name: "暂停", exact: true }).click();
    await expect(row).toContainText("已暂停");
    await row.getByRole("button", { name: "核对启用", exact: true }).click();
    const dialog = page.getByRole("dialog");
    await expect(dialog).toContainText("300元/晚");
    await expect(
      dialog.getByRole("button", { name: "确认启用", exact: true }),
    ).toBeDisabled();
    await dialog
      .getByText("我已核对原文、适用范围、单位及单价一致", { exact: true })
      .click();
    await expect(dialog.getByRole("checkbox")).toBeChecked();
    await dialog.getByRole("button", { name: "确认启用", exact: true }).click();
    await expect(row).toContainText("已启用");
    await expect(
      page.locator(".el-dialog").filter({ hasText: "核对原文并启用规则" }),
    ).toBeHidden();
    await expect(page.getByText("规则已启用", { exact: true })).toBeHidden();
    await page.screenshot({
      path: "../.m5/regression-m4-rules-desktop.png",
      fullPage: true,
    });
  } finally {
    const current = (
      await (await request.get("/api/v1/admin/fee-rules", { headers })).json()
    ).data.items.find((r: { id: string }) => r.id === original.id);
    if (current.status !== "active")
      await request.post(`/api/v1/admin/fee-rules/${current.id}/activate`, {
        headers,
        data: { expected_revision: current.revision, source_checked: true },
      });
  }
});
