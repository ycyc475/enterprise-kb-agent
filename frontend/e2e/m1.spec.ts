import { test, expect, checkpointMembership } from "./fixtures";
import type { Page } from "@playwright/test";
import { readFileSync } from "node:fs";

const credentials = JSON.parse(
  readFileSync(
    new URL("../../data/runtime/demo-credentials.json", import.meta.url),
    "utf8",
  ),
) as Record<string, string>;
const origin = "http://127.0.0.1:5173";
async function login(page: Page, username: string) {
  await page.goto("/login");
  await page.getByLabel("账号", { exact: true }).fill(username);
  await page.getByLabel("密码", { exact: true }).fill(credentials[username]!);
  await page.getByRole("button", { name: "登录", exact: true }).click();
  await expect(page).toHaveURL(origin + "/");
  await expect(page.getByRole("textbox", { name: "输入问题" })).toBeVisible();
}

test("管理员可登录并访问三个管理页面", async ({ page }) => {
  const errors: string[] = [];
  page.on("pageerror", (e) => errors.push(e.message));
  await page.goto("/login");
  await page.screenshot({
    path: "../.m5/regression-m1-login.png",
    fullPage: true,
    animations: "disabled",
  });
  await login(page, "admin");
  await page.getByRole("link", { name: "账号管理" }).click();
  await expect(
    page.getByRole("heading", { name: "账号管理", exact: true }),
  ).toBeVisible();
  await expect(
    page.getByRole("cell", { name: "employee_li", exact: true }),
  ).toBeVisible();
  await page.getByRole("button", { name: "＋ 创建账号" }).click();
  await expect(
    page.getByRole("dialog", { name: "创建企业账号" }),
  ).toBeVisible();
  await page.getByRole("button", { name: "取消", exact: true }).click();
  await page.getByRole("link", { name: "知识库管理" }).click();
  await expect(page.getByRole("cell", { name: "企业公共制度" })).toBeVisible();
  await page
    .getByRole("row")
    .filter({ hasText: "研发团队资料" })
    .getByRole("button", { name: "管理授权" })
    .click();
  await expect(
    page.getByRole("dialog", { name: "管理知识库授权" }),
  ).toBeVisible();
  await expect(page.getByRole("checkbox")).toHaveCount(2);
  await page.getByRole("button", { name: "取消", exact: true }).click();
  await page.getByRole("link", { name: "员工资料", exact: false }).click();
  await page.getByRole("textbox", { name: "搜索员工" }).fill("李同学");
  await expect(
    page.getByRole("cell", { name: "employee_li", exact: true }),
  ).toBeVisible();
  await expect(
    page.getByRole("cell", { name: "employee_wang", exact: true }),
  ).toHaveCount(0);
  await page.getByRole("button", { name: "编辑资料" }).click();
  await expect(page.getByLabel("姓名", { exact: true })).toHaveValue("李同学");
  await page.getByRole("button", { name: "取消", exact: true }).click();
  expect(errors).toEqual([]);
});

test("员工仅看到授权知识库，刷新恢复登录且不能打开管理路由", async ({
  page,
  context,
}) => {
  await login(page, "employee_li");
  await expect(page.locator(".scope-card .kb-summary")).toHaveCount(1);
  await expect(page.getByRole("link", { name: "账号管理" })).toHaveCount(0);
  await page.reload();
  await expect(page.getByRole("textbox", { name: "输入问题" })).toBeVisible();
  const another = await context.newPage();
  await Promise.all([another.goto(origin + "/"), page.reload()]);
  await expect(
    another.getByRole("textbox", { name: "输入问题" }),
  ).toBeVisible();
  await expect(page.getByRole("textbox", { name: "输入问题" })).toBeVisible();
  await another.close();
  await page.goto("/admin/users");
  await expect(page).toHaveURL(origin + "/");
  await page.screenshot({
    path: "../.m5/regression-m1-workspace.png",
    fullPage: true,
    animations: "disabled",
  });
  const storage = await page.evaluate(() => ({
    local: { ...localStorage },
    session: { ...sessionStorage },
    cookie: document.cookie,
  }));
  expect(storage).toEqual({ local: {}, session: {}, cookie: "" });
  await page
    .locator(".sidebar")
    .getByRole("link", { name: "个人资料" })
    .click();
  await expect(
    page.getByRole("heading", { name: "员工资料", exact: true }),
  ).toBeVisible();
  await expect(page.getByText("李同学", { exact: true }).last()).toBeVisible();
  await page.getByRole("button", { name: "退出", exact: true }).click();
  await expect(page).toHaveURL(origin + "/login");
  await page.goto("/");
  await expect(page).toHaveURL(origin + "/login");
});

test("管理员在页面撤销与恢复授权，对已有员工会话立即生效", async ({
  browser,
  playwright,
}) => {
  const adminContext = await browser.newContext({ baseURL: origin });
  const employeeContext = await browser.newContext({ baseURL: origin });
  const api = await playwright.request.newContext({
    baseURL: origin,
    extraHTTPHeaders: { Origin: origin },
  });
  const response = await api.post("/api/v1/auth/login", {
    data: { username: "admin", password: credentials.admin },
  });
  expect(response.status()).toBe(200);
  const adminAuth = {
    Authorization: "Bearer " + (await response.json()).data.access_token,
  };
  const bases = (
    await (
      await api.get("/api/v1/knowledge-bases", { headers: adminAuth })
    ).json()
  ).data.items;
  const kb = bases.find((k: { name: string }) => k.name === "企业公共制度");
  const endpoint = `/api/v1/admin/knowledge-bases/${kb.id}/members`;
  const original = (
    await (await api.get(endpoint, { headers: adminAuth })).json()
  ).data.user_ids;
  const people = (
    await (await api.get("/api/v1/admin/users", { headers: adminAuth })).json()
  ).data.items;
  const employeeId = people.find(
    (user: { username: string }) => user.username === "employee_li",
  ).id;
  checkpointMembership(
    kb.id,
    original,
    original.filter((id: string) => id !== employeeId),
  );
  try {
    const admin = await adminContext.newPage(),
      employee = await employeeContext.newPage();
    await login(admin, "admin");
    await login(employee, "employee_li");
    await admin.getByRole("link", { name: "知识库管理" }).click();
    await admin
      .getByRole("row")
      .filter({ hasText: "企业公共制度" })
      .getByRole("button", { name: "管理授权" })
      .click();
    await expect(admin.getByRole("checkbox", { name: /李同学/ })).toBeChecked();
    await admin
      .getByRole("dialog")
      .getByText("李同学", { exact: true })
      .click({ timeout: 5000 });
    await expect(
      admin.getByRole("checkbox", { name: /李同学/ }),
    ).not.toBeChecked();
    await admin.getByRole("button", { name: "保存授权", exact: true }).click();
    await expect(admin.getByRole("dialog")).toHaveCount(0);
    await employee.getByRole("button", { name: "刷新可访问知识库" }).click();
    await expect(employee.locator(".scope-card .kb-summary")).toHaveCount(0);
    await expect(
      employee.getByText("还没有授权知识库，请联系管理员。"),
    ).toBeVisible();
  } finally {
    const current = (
      await (await api.get(endpoint, { headers: adminAuth })).json()
    ).data;
    const restored = await api.put(endpoint, {
      headers: adminAuth,
      data: { user_ids: original, expected_revision: current.revision },
    });
    expect(restored.status()).toBe(200);
    await api.dispose();
    await adminContext.close();
    await employeeContext.close();
  }
});

test("窄屏工作台可用且无水平溢出", async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await login(page, "employee_wang");
  const overflow = await page.evaluate(
    () => document.documentElement.scrollWidth > innerWidth,
  );
  expect(overflow).toBe(false);
  await page.screenshot({
    path: "../.m5/regression-m1-workspace-mobile.png",
    fullPage: true,
    animations: "disabled",
  });
});
