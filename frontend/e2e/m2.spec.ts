import { test, expect } from "./fixtures";
import type { Page } from "@playwright/test";
import { readFileSync } from "node:fs";
const credentials = JSON.parse(
  readFileSync(
    new URL("../../data/runtime/demo-credentials.json", import.meta.url),
    "utf8",
  ),
) as Record<string, string>;
async function login(page: Page, username: string) {
  await page.goto("/login");
  await page.getByLabel("账号", { exact: true }).fill(username);
  await page.getByLabel("密码", { exact: true }).fill(credentials[username]!);
  const response = page.waitForResponse(
    (r) =>
      r.url().endsWith("/api/v1/auth/login") && r.request().method() === "POST",
  );
  await page.getByRole("button", { name: "登录", exact: true }).click();
  const data = (await (await response).json()).data;
  await expect(page).toHaveURL("http://127.0.0.1:5173/");
  return data;
}
test("M2管理员上传、核对、替换失败、重试、成功发布与删除", async ({
  page,
  request,
}) => {
  test.setTimeout(60000);
  const user = await login(page, "admin");
  const headers = { Authorization: "Bearer " + user.access_token };
  const kb = user.user.knowledge_bases.find(
    (k: { name: string }) => k.name === "企业公共制度",
  );
  const title = "M2浏览器验证-" + Date.now();
  let documentId = "";
  await page.goto("/knowledge?kb=" + kb.id);
  async function file(name: string, content: string) {
    await page.getByLabel("选择文件").setInputFiles({
      name,
      mimeType: "application/octet-stream",
      buffer: Buffer.from(content),
    });
    await page.getByRole("button", { name: "上传并建立索引" }).click();
  }
  try {
    await page.getByRole("button", { name: "＋ 上传文档" }).click();
    await page.getByLabel("文档标题").fill(title);
    const uploaded = page.waitForResponse(
      (r) => r.url().endsWith("/documents") && r.request().method() === "POST",
    );
    await file("m2-ui.md", "# 核对样例\n\n铁路出差报销需要火车票和行程记录。");
    documentId = (await (await uploaded).json()).data.document_id;
    const row = page.getByRole("row").filter({ hasText: title });
    await expect(row.getByText("已就绪", { exact: true })).toBeVisible({
      timeout: 20000,
    });
    await row.getByRole("button", { name: "核对片段" }).click();
    await expect(
      page.locator(".source-block").filter({ hasText: "火车票" }),
    ).toBeVisible();
    await page.getByRole("button", { name: "关闭此对话框" }).click();
    await row.getByRole("button", { name: "替换", exact: true }).click();
    await file("broken.pdf", "not a PDF");
    await expect(row.getByText("处理失败", { exact: true })).toBeVisible({
      timeout: 10000,
    });
    await expect(
      row.getByRole("cell", { name: "v1", exact: true }),
    ).toBeVisible();
    await row.getByRole("button", { name: "重试", exact: true }).click();
    await expect(row.getByText("处理失败", { exact: true })).toBeVisible({
      timeout: 10000,
    });
    await row.getByRole("button", { name: "替换", exact: true }).click();
    await file("updated.md", "# 更新样例\n\n铁路出差报销还应附上审批记录。");
    await expect(row.getByText("已就绪", { exact: true })).toBeVisible({
      timeout: 20000,
    });
    await expect(
      row.getByRole("cell", { name: "v3", exact: true }),
    ).toBeVisible();
    await row.getByRole("button", { name: "删除", exact: true }).click();
    await page.getByRole("button", { name: "删除文档", exact: true }).click();
    await expect(row).toHaveCount(0);
  } finally {
    if (documentId)
      expect([204, 404]).toContain(
        (
          await request.delete(`/api/v1/admin/documents/${documentId}`, {
            headers,
          })
        ).status(),
      );
  }
});
test("M2员工真实检索、引用抽屉与鉴权下载", async ({ page, request }) => {
  test.setTimeout(45000);
  const user = await login(page, "employee_li");
  const kb = user.user.knowledge_bases.find(
    (k: { name: string }) => k.name === "企业公共制度",
  );
  await page.getByRole("link", { name: "文档检索" }).click();
  await expect(
    page.locator(".el-table").getByText("M2技术样例-差旅标准", { exact: true }),
  ).toBeVisible();
  await page.getByRole("textbox", { name: "检索问题" }).fill("北京住宿标准");
  await page.getByRole("button", { name: "检索文档", exact: true }).click();
  await expect(
    page.locator(".result-card").filter({ hasText: "300元" }).first(),
  ).toBeVisible({ timeout: 30000 });
  await expect(
    page
      .locator(".result-card")
      .filter({ hasText: "关键词命中 · 语义召回" })
      .first(),
  ).toBeVisible();
  await page.screenshot({
    path: "../.m5/regression-m2-search-desktop.png",
    fullPage: true,
    animations: "disabled",
  });
  await page
    .locator(".result-card")
    .filter({ hasText: "300元" })
    .first()
    .getByRole("button", { name: "查看来源" })
    .click();
  await expect(page.locator(".source-block pre")).toContainText("300元");
  const downloaded = page.waitForEvent("download");
  await page.getByRole("button", { name: "下载原文件" }).click();
  expect((await downloaded).suggestedFilename()).toBeTruthy();
  await page.getByRole("button", { name: "关闭此对话框" }).click();
  await expect(page.locator(".el-drawer")).not.toBeVisible();
  const list = await request.get(`/api/v1/knowledge-bases/${kb.id}/documents`, {
    headers: { Authorization: "Bearer " + user.access_token },
  });
  const doc = (await list.json()).data.items[0];
  expect(
    (
      await request.get(
        `/api/v1/documents/${doc.id}/versions/${doc.current_version.id}/download`,
      )
    ).status(),
  ).toBe(401);
  await page.setViewportSize({ width: 390, height: 844 });
  await page.screenshot({
    path: "../.m5/regression-m2-search-mobile.png",
    fullPage: true,
    animations: "disabled",
  });
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth > innerWidth,
    ),
  ).toBe(false);
});
