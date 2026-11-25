import { test, expect } from "./fixtures";
import { readFileSync } from "node:fs";
import { resolve } from "node:path";

const credentials = JSON.parse(
  readFileSync(
    new URL("../../data/runtime/demo-credentials.json", import.meta.url),
    "utf8",
  ),
) as Record<string, string>;

test.use({ video: { mode: "on", size: { width: 1440, height: 1000 } } });

test("M6演示：真实办理指南、确认预算、引用和移动布局", async ({
  page,
  request,
  context,
}) => {
  test.setTimeout(150000);
  const video = page.video();
  let id = "";
  let headers: Record<string, string> = {};
  try {
    await page.goto("/login");
    await page.getByLabel("账号", { exact: true }).fill("employee_li");
    await page
      .getByLabel("密码", { exact: true })
      .fill(credentials.employee_li!);
    const loggedIn = page.waitForResponse(
      (r) =>
        r.url().endsWith("/api/v1/auth/login") &&
        r.request().method() === "POST",
    );
    await page.getByRole("button", { name: "登录", exact: true }).click();
    headers = {
      Authorization:
        "Bearer " + (await (await loggedIn).json()).data.access_token,
    };
    const input = page.getByRole("textbox", { name: "输入问题" });
    await expect(input).toBeVisible();
    await input.fill("根据虚构演示制度，从申请到报销需要哪些步骤？");
    await page.getByRole("button", { name: "发送 ↑", exact: true }).click();
    await expect(page).toHaveURL(/\/chat\/[0-9a-f-]+/);
    id = page.url().split("/").pop()!;
    const first = page.getByTestId("assistant-message").last();
    await expect(first).toContainText("批准", { timeout: 45000 });
    await expect(first).toContainText("审核");
    await page.screenshot({
      path: "../.m6/demo-guide.png",
      fullPage: true,
      animations: "disabled",
    });
    await page.waitForTimeout(1600); // Leave a readable frame in the demonstration video.
    await input.fill("我去北京出差3天，住宿2晚，请估算住宿和补贴预算。");
    await page.getByRole("button", { name: "发送 ↑", exact: true }).click();
    const proposal = page.getByTestId("trip-proposal").last();
    await expect(proposal).toContainText("北京", { timeout: 45000 });
    await expect(proposal).toContainText("3天");
    await expect(proposal).toContainText("2晚");
    await page.waitForTimeout(1600);
    await proposal
      .getByRole("button", { name: "确认条件并计算", exact: true })
      .click();
    const budget = page.getByTestId("budget-result").last();
    await expect(budget).toContainText("¥840.00", { timeout: 45000 });
    await expect(budget).toContainText("¥600.00");
    await expect(budget).toContainText("¥240.00");
    await page.screenshot({
      path: "../.m6/demo-budget.png",
      fullPage: true,
      animations: "disabled",
    });
    await page
      .getByTestId("assistant-message")
      .last()
      .locator(".answer-citations button")
      .first()
      .click();
    await expect(page.locator(".citation-detail blockquote")).not.toBeEmpty();
    await page.screenshot({
      path: "../.m6/demo-source.png",
      animations: "disabled",
    });
    await page.waitForTimeout(2000);
    await page.getByRole("button", { name: "关闭此对话框" }).click();
    await page.setViewportSize({ width: 390, height: 844 });
    await expect(budget).toContainText("¥840.00");
    expect(
      await page.evaluate(
        () => document.documentElement.scrollWidth <= innerWidth,
      ),
    ).toBe(true);
    await page.screenshot({
      path: "../.m6/demo-mobile.png",
      fullPage: true,
      animations: "disabled",
    });
    await page.waitForTimeout(1600);
  } finally {
    if (id) await request.delete(`/api/v1/conversations/${id}`, { headers });
    await context.close();
    if (video) await video.saveAs(resolve("../.m6/demo.webm"));
  }
});
