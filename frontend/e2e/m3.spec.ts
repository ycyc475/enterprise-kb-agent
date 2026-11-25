import { test, expect, checkpointMembership } from "./fixtures";
import { readFileSync } from "node:fs";
const credentials = JSON.parse(
  readFileSync(
    new URL("../../data/runtime/demo-credentials.json", import.meta.url),
    "utf8",
  ),
) as Record<string, string>;
const origin = "http://127.0.0.1:5173";
async function login(page: import("@playwright/test").Page) {
  await page.goto("/login");
  await page.getByLabel("账号", { exact: true }).fill("employee_li");
  await page.getByLabel("密码", { exact: true }).fill(credentials.employee_li!);
  const response = page.waitForResponse(
    (r) =>
      r.url().endsWith("/api/v1/auth/login") && r.request().method() === "POST",
  );
  await page.getByRole("button", { name: "登录", exact: true }).click();
  const data = (await (await response).json()).data;
  await expect(page.getByRole("textbox", { name: "输入问题" })).toBeVisible();
  return data;
}
test("M3真实多轮问答、引用、条件保存、刷新恢复与撤权历史遮蔽", async ({
  page,
  request,
}) => {
  test.setTimeout(90000);
  const identity = await login(page),
    userHeaders = { Authorization: "Bearer " + identity.access_token };
  const adminResponse = await request.post("/api/v1/auth/login", {
    headers: { Origin: origin },
    data: { username: "admin", password: credentials.admin },
  });
  const adminHeaders = {
    Authorization: "Bearer " + (await adminResponse.json()).data.access_token,
  };
  const kb = identity.user.knowledge_bases.find(
    (k: { name: string }) => k.name === "企业公共制度",
  );
  const endpoint = `/api/v1/admin/knowledge-bases/${kb.id}/members`;
  const original = (
    await (await request.get(endpoint, { headers: adminHeaders })).json()
  ).data;
  let id = "";
  try {
    await page
      .getByRole("textbox", { name: "输入问题" })
      .fill("根据技术样例，北京S1员工的住宿标准是多少？");
    await page.getByRole("button", { name: "发送 ↑", exact: true }).click();
    await expect(page).toHaveURL(/\/chat\/[0-9a-f-]+/);
    id = page.url().split("/").pop()!;
    await expect(page.getByTestId("assistant-message").first()).toContainText(
      "300",
      { timeout: 40000 },
    );
    await expect(page.locator(".run-status")).toContainText("回答已完成");
    await page.locator(".answer-citations button").first().click();
    await expect(page.locator(".citation-detail blockquote")).not.toBeEmpty();
    const download = page.waitForEvent("download");
    await page.getByRole("button", { name: "下载原文件", exact: true }).click();
    expect((await download).suggestedFilename()).toBeTruthy();
    await page.getByRole("button", { name: "关闭此对话框" }).click();
    await page.getByLabel("目的地", { exact: true }).fill("北京");
    await page.getByRole("spinbutton", { name: "出差天数" }).fill("3");
    await page.getByRole("spinbutton", { name: "住宿晚数" }).fill("2");
    await page.getByRole("button", { name: "保存条件", exact: true }).click();
    await expect(
      page.getByText("本次条件已保存", { exact: true }),
    ).toBeVisible();
    await page
      .getByRole("textbox", { name: "输入问题" })
      .fill("那技术样例中的每日补贴呢？");
    await page.getByRole("button", { name: "发送 ↑", exact: true }).click();
    await expect(page.getByTestId("assistant-message").nth(1)).toContainText(
      "80",
      { timeout: 40000 },
    );
    await page.reload();
    await expect(page.getByTestId("assistant-message")).toHaveCount(2);
    await expect(
      page.getByRole("spinbutton", { name: "出差天数" }),
    ).toHaveValue("3");
    await expect(page.locator(".run-status")).toContainText("回答已完成");
    expect(
      (
        await request.get(`/api/v1/conversations/${id}/messages`, {
          headers: adminHeaders,
        })
      ).status(),
    ).toBe(404);
    await page.screenshot({
      path: "../.m5/regression-m3-chat-desktop.png",
      fullPage: true,
      animations: "disabled",
    });
    await page.setViewportSize({ width: 390, height: 844 });
    expect(
      await page.evaluate(
        () => document.documentElement.scrollWidth > innerWidth,
      ),
    ).toBe(false);
    await page.screenshot({
      path: "../.m5/regression-m3-chat-mobile.png",
      fullPage: true,
      animations: "disabled",
    });
    const people = (
      await (
        await request.get("/api/v1/admin/users", { headers: adminHeaders })
      ).json()
    ).data.items;
    const employee = people.find(
      (p: { username: string }) => p.username === "employee_li",
    );
    const current = (
      await (await request.get(endpoint, { headers: adminHeaders })).json()
    ).data;
    checkpointMembership(
      kb.id,
      original.user_ids,
      current.user_ids.filter((uid: string) => uid !== employee.id),
    );
    expect(
      (
        await request.put(endpoint, {
          headers: adminHeaders,
          data: {
            user_ids: current.user_ids.filter(
              (uid: string) => uid !== employee.id,
            ),
            expected_revision: current.revision,
          },
        })
      ).status(),
    ).toBe(200);
    await page.reload();
    await expect(page.locator(".unavailable-message")).toHaveCount(2);
    await expect(page.locator(".answer-citations")).toHaveCount(0);
  } finally {
    const latest = (
      await (await request.get(endpoint, { headers: adminHeaders })).json()
    ).data;
    expect(
      (
        await request.put(endpoint, {
          headers: adminHeaders,
          data: {
            user_ids: original.user_ids,
            expected_revision: latest.revision,
          },
        })
      ).status(),
    ).toBe(200);
    if (id)
      expect(
        (
          await request.delete(`/api/v1/conversations/${id}`, {
            headers: userHeaders,
          })
        ).status(),
      ).toBe(204);
  }
});
test("M3取消本轮并删除会话", async ({ page }) => {
  test.setTimeout(45000);
  await login(page);
  await page
    .getByRole("textbox", { name: "输入问题" })
    .fill("综合技术样例说明住宿发票要求和每日补贴标准。");
  await page.getByRole("button", { name: "发送 ↑", exact: true }).click();
  await page.getByRole("button", { name: "取消本轮", exact: true }).click();
  await expect(page.locator(".run-status")).toContainText("已取消", {
    timeout: 10000,
  });
  await page.getByRole("button", { name: "删除", exact: true }).click();
  await page.getByRole("button", { name: "删除会话", exact: true }).click();
  await expect(page).toHaveURL(origin + "/");
  await expect(page.getByTestId("user-message")).toHaveCount(0);
});
