import { test, expect } from "@playwright/test";
import { readFileSync } from "node:fs";

const credentials = JSON.parse(
  readFileSync(
    new URL("../../data/runtime/demo-credentials.json", import.meta.url),
    "utf8",
  ),
) as Record<string, string>;

test("四类费用的本地确认、程序计算、原文引用及移动页面", async ({
  page,
  request,
}) => {
  test.setTimeout(60000);
  page.setDefaultTimeout(8000);
  await page.goto("/login");
  await page.getByLabel("账号", { exact: true }).fill("employee_wang");
  await page
    .getByLabel("密码", { exact: true })
    .fill(credentials.employee_wang!);
  const loggedIn = page.waitForResponse(
    (r) =>
      r.url().endsWith("/api/v1/auth/login") && r.request().method() === "POST",
  );
  await page.getByRole("button", { name: "登录", exact: true }).click();
  const headers = {
    Authorization:
      "Bearer " + (await (await loggedIn).json()).data.access_token,
  };
  const kbs = (
    await (await request.get("/api/v1/knowledge-bases", { headers })).json()
  ).data.items;
  const common = kbs.find((kb: { name: string }) => kb.name === "企业公共制度");
  expect(common).toBeTruthy();
  const created = await request.post("/api/v1/conversations", {
    headers,
    data: { title: "综合费用功能复测", knowledge_base_ids: [common.id] },
  });
  expect(created.status()).toBe(201);
  const conv = (await created.json()).data;
  try {
    await page.goto("/chat/" + conv.id);
    const form = page.locator(".trip-card").filter({
      has: page.getByRole("heading", { name: "其他费用核算", exact: true }),
    });
    for (const [label, quantity, amount, expected] of [
      ["办公用品采购", "12", "2400", "1500.00"],
      ["培训报销", "3", "1234.56", "987.65"],
      ["业务招待", "15", "3000", "2000.00"],
    ]) {
      await expect(
        form.getByRole("textbox", { name: "实际申请金额", exact: true }),
      ).toBeEnabled();
      await expect(
        form.getByRole("spinbutton", { name: "费用数量" }),
      ).toBeEnabled();
      await form.locator(".el-select__wrapper").click();
      await page.getByRole("option", { name: label!, exact: true }).click();
      await form.getByRole("spinbutton", { name: "费用数量" }).fill(quantity!);
      await form
        .getByRole("textbox", { name: "实际申请金额", exact: true })
        .fill(amount!);
      await form
        .getByRole("button", { name: "确认条件并核算", exact: true })
        .click();
      const result = page.getByTestId("expense-result").last();
      await expect(result).toContainText("¥" + expected);
      await expect(page.getByTestId("assistant-message").last()).toContainText(
        "虚构演示制度-综合费用核算与报销",
      );
    }
    await page.getByLabel("目的地", { exact: true }).fill("北京");
    await page.getByRole("spinbutton", { name: "出差天数" }).fill("3");
    await page.getByRole("spinbutton", { name: "住宿晚数" }).fill("2");
    await page
      .getByRole("button", { name: "确认并计算预算", exact: true })
      .click();
    await expect(page.getByTestId("budget-result").last()).toContainText(
      "¥1240.00",
    );
    await page.screenshot({
      path: "../.cache/expense_policy/ui-desktop.png",
      fullPage: true,
    });
    await page
      .getByTestId("assistant-message")
      .last()
      .locator(".answer-citations button")
      .first()
      .click();
    await expect(page.locator(".citation-detail blockquote")).toContainText(
      "TRV-",
    );
    await page.getByRole("button", { name: "关闭此对话框" }).click();
    await page.reload();
    await expect(page.getByTestId("expense-result").last()).toContainText(
      "¥2000.00",
    );
    await page.setViewportSize({ width: 390, height: 844 });
    await page.screenshot({
      path: "../.cache/expense_policy/ui-mobile.png",
      fullPage: true,
    });
    expect(
      await page.evaluate(
        () => document.documentElement.scrollWidth <= window.innerWidth,
      ),
    ).toBeTruthy();
  } finally {
    const removed = await request.delete("/api/v1/conversations/" + conv.id, {
      headers,
    });
    expect(removed.status()).toBe(204);
  }
});
