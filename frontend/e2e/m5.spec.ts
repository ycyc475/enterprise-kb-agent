import { test, expect } from "./fixtures";
import type { Page, APIRequestContext, APIResponse } from "@playwright/test";
import { readFileSync, writeFileSync } from "node:fs";
import { createHash } from "node:crypto";
const credentials = JSON.parse(
  readFileSync(
    new URL("../../data/runtime/demo-credentials.json", import.meta.url),
    "utf8",
  ),
) as Record<string, string>;
const origin = "http://127.0.0.1:5173";
async function data(response: APIResponse, status = 200) {
  expect(response.status()).toBe(status);
  return status === 204 ? null : (await response.json()).data;
}
async function login(page: Page, username = "admin") {
  await page.goto("/login");
  await page.getByLabel("账号", { exact: true }).fill(username);
  await page.getByLabel("密码", { exact: true }).fill(credentials[username]!);
  const response = page.waitForResponse(
    (r) =>
      r.url().endsWith("/api/v1/auth/login") && r.request().method() === "POST",
  );
  await page.getByRole("button", { name: "登录", exact: true }).click();
  const value = await data(await response);
  await expect(page.getByRole("textbox", { name: "输入问题" })).toBeVisible();
  return value;
}
async function waitDocument(
  request: APIRequestContext,
  headers: Record<string, string>,
  kb: string,
  id: string,
  versionId?: string,
) {
  for (let n = 0; n < 100; n++) {
    const docs = (
      await data(
        await request.get(`/api/v1/knowledge-bases/${kb}/documents`, {
          headers,
          timeout: 10000,
        }),
      )
    ).items;
    const doc = docs.find((d: { id: string }) => d.id === id);
    if (
      doc &&
      (!versionId || doc.latest_version?.id === versionId) &&
      !["queued", "processing"].includes(doc.job?.status)
    )
      return doc;
    await new Promise((resolve) => setTimeout(resolve, 300));
  }
  throw new Error(
    "Document processing did not finish within the verification interval",
  );
}

test("M5制度更新、规则暂停、核对恢复880元、历史遮蔽并恢复演示基线", async ({
  page,
  request,
}) => {
  test.setTimeout(180000);
  page.setDefaultTimeout(10000);
  const identity = await login(page),
    headers = { Authorization: "Bearer " + identity.access_token };
  const kb = identity.user.knowledge_bases.find(
    (k: { name: string }) => k.name === "企业公共制度",
  );
  const docs = (
    await data(
      await request.get(`/api/v1/knowledge-bases/${kb.id}/documents`, {
        headers,
      }),
    )
  ).items;
  const original = docs.find(
    (d: { title: string }) => d.title === "虚构演示制度-住宿标准",
  );
  const rules = (
    await data(await request.get("/api/v1/admin/fee-rules", { headers }))
  ).items.filter(
    (r: { source_document_id: string }) => r.source_document_id === original.id,
  );
  expect(rules).toHaveLength(4);
  expect(
    rules.every((r: { status: string }) => r.status === "active"),
  ).toBeTruthy();
  const bodyResponse = await request.get(
    `/api/v1/documents/${original.id}/versions/${original.current_version.id}/download`,
    { headers },
  );
  expect(bodyResponse.status()).toBe(200);
  const originalBody = await bodyResponse.body();
  const originalHash = createHash("sha256").update(originalBody).digest("hex");
  writeFileSync(
    new URL("../../.m5/original-travel.md", import.meta.url),
    originalBody,
  );
  writeFileSync(
    new URL("../../.m5/demo-checkpoint.json", import.meta.url),
    JSON.stringify(
      {
        document_id: original.id,
        kb_id: kb.id,
        original_name: original.current_version.original_name,
        sha256: originalHash,
        rules,
      },
      null,
      2,
    ),
  );
  const changed = originalBody.toString("utf8").replace("300元/晚", "320元/晚");
  expect(changed).not.toBe(originalBody.toString("utf8"));
  const employee = await data(
    await request.post("/api/v1/auth/login", {
      headers: { Origin: origin },
      data: { username: "employee_li", password: credentials.employee_li },
    }),
  );
  const userHeaders = { Authorization: "Bearer " + employee.access_token };
  let conv: any = null;
  const report: Record<string, unknown> = {
    source_sha256_before: originalHash,
    source_document_id: original.id,
    passed: false,
  };
  async function calculate() {
    const response = await data(
      await request.post(`/api/v1/conversations/${conv.id}/calculations`, {
        headers: userHeaders,
        data: {
          client_request_id: crypto.randomUUID(),
          context_revision: conv.context_revision,
          profile_revision: employee.user.profile.revision,
          trip_context: {
            destination: "北京",
            travel_days: 3,
            lodging_nights: 2,
          },
        },
      }),
      201,
    );
    conv = response.conversation;
    return response.calculation;
  }
  try {
    conv = await data(
      await request.post("/api/v1/conversations", {
        headers: userHeaders,
        data: { title: "M5更新恢复验证", knowledge_base_ids: [kb.id] },
      }),
      201,
    );
    const before = await calculate();
    expect(before.total_amount).toBe("840.00");
    report.before = before.total_amount;
    await page.goto("/knowledge?kb=" + kb.id);
    const docRow = page
      .getByRole("row")
      .filter({ hasText: "虚构演示制度-住宿标准" });
    await docRow.getByRole("button", { name: "替换", exact: true }).click();
    await page.getByLabel("选择文件").setInputFiles({
      name: "m5-updated-travel.md",
      mimeType: "text/markdown",
      buffer: Buffer.from(changed),
    });
    const uploaded = page.waitForResponse(
      (r) =>
        r.url().endsWith(`/documents/${original.id}/versions`) &&
        r.request().method() === "POST",
    );
    await page.getByRole("button", { name: "上传并建立索引" }).click();
    const update = await data(await uploaded, 202);
    await waitDocument(request, headers, kb.id, original.id, update.version_id);
    const hidden = await data(
      await request.get(`/api/v1/conversations/${conv.id}/messages`, {
        headers: userHeaders,
      }),
    );
    expect(hidden.items.at(-1).status).toBe("source_unavailable");
    expect(hidden.items.at(-1).content).toBeNull();
    expect(
      (
        await request.get(`/api/v1/calculations/${before.id}`, {
          headers: userHeaders,
        })
      ).status(),
    ).toBe(404);
    const blocked = await calculate();
    expect(blocked.total_amount).toBeNull();
    report.during = blocked.status;
    await docRow.getByRole("link", { name: "关联规则", exact: true }).click();
    const row = page
      .getByRole("row")
      .filter({ hasText: "演示-S1-北京上海住宿" });
    await expect(row).toContainText("制度版本已更新");
    await row.getByRole("button", { name: "核对恢复", exact: true }).click();
    const dialog = page.getByRole("dialog", {
      name: "核对当前制度并恢复规则",
      exact: true,
    });
    await expect(dialog).toContainText("300元/晚");
    await dialog.getByTestId("recovery-source-select").click();
    await page.getByRole("option").filter({ hasText: "320元/晚" }).click();
    await dialog.getByRole("textbox", { name: "恢复规则单价" }).fill("320.00");
    await expect(
      dialog.getByRole("button", { name: "确认恢复规则", exact: true }),
    ).toBeDisabled();
    await dialog
      .getByText("我已核对新条款、适用范围、单位及单价一致", { exact: true })
      .click();
    await page.screenshot({
      path: "../.m5/source-review-desktop.png",
      fullPage: false,
      animations: "disabled",
    });
    await dialog
      .getByRole("button", { name: "确认恢复规则", exact: true })
      .click();
    await expect(dialog).toBeHidden();
    await expect(row).toContainText("320.00");
    const after = await calculate();
    expect(after.total_amount).toBe("880.00");
    report.after = after.total_amount;
    await row.getByRole("button", { name: "历史", exact: true }).click();
    await expect(
      page
        .locator(".audit-table")
        .getByText("核对新来源并恢复规则", { exact: true })
        .first(),
    ).toBeVisible();
    await page.screenshot({
      path: "../.m5/rule-history-desktop.png",
      fullPage: false,
      animations: "disabled",
    });
    report.history_hidden = true;
    report.passed = true;
  } finally {
    const current = await waitDocument(request, headers, kb.id, original.id);
    if (current.current_version.id !== original.current_version.id) {
      const restored = await data(
        await request.post(`/api/v1/admin/documents/${original.id}/versions`, {
          headers,
          multipart: {
            expected_current_version_id: current.current_version.id,
            file: {
              name: original.current_version.original_name,
              mimeType: "text/markdown",
              buffer: originalBody,
            },
          },
        }),
        202,
      );
      await waitDocument(
        request,
        headers,
        kb.id,
        original.id,
        restored.version_id,
      );
    }
    for (const rule of rules) {
      const value = await data(
        await request.get(`/api/v1/admin/fee-rules/${rule.id}/source-review`, {
          headers,
        }),
      );
      if (value.rule.status === "active") continue;
      const source = value.candidates.find(
        (c: { content: string }) =>
          c.content.includes(rule.cities[0]) &&
          c.content.includes(String(Number(rule.unit_rate))),
      );
      expect(source).toBeTruthy();
      await data(
        await request.post(`/api/v1/admin/fee-rules/${rule.id}/recover`, {
          headers,
          data: {
            expected_revision: value.rule.revision,
            expected_source_version_id: value.current_version_id,
            source_chunk_id: source.chunk_id,
            source_quote: source.content,
            unit_rate: rule.unit_rate,
            source_checked: true,
          },
        }),
      );
    }
    if (conv) {
      const final = await calculate();
      expect(final.total_amount).toBe("840.00");
      report.restored = final.total_amount;
      await data(
        await request.delete(`/api/v1/conversations/${conv.id}`, {
          headers: userHeaders,
        }),
        204,
      );
    }
    const finalDoc = await waitDocument(request, headers, kb.id, original.id);
    const finalBytes = await request.get(
      `/api/v1/documents/${original.id}/versions/${finalDoc.current_version.id}/download`,
      { headers },
    );
    expect(finalBytes.status()).toBe(200);
    report.source_sha256_after = createHash("sha256")
      .update(await finalBytes.body())
      .digest("hex");
    expect(report.source_sha256_after).toBe(originalHash);
    writeFileSync(
      new URL("../../.m5/update-recovery.json", import.meta.url),
      JSON.stringify(report, null, 2),
    );
  }
});

test("M5管理变更详情、用量页面与员工访问边界", async ({ page, request }) => {
  test.setTimeout(45000);
  await login(page);
  await page.getByRole("link", { name: "操作历史", exact: true }).click();
  await expect(
    page.getByRole("heading", { name: "操作历史", exact: true }),
  ).toBeVisible();
  await expect(page.locator(".audit-table tbody tr").first()).toBeVisible();
  await page
    .getByRole("button", { name: "查看变更", exact: true })
    .first()
    .click();
  await expect(
    page.getByRole("heading", { name: "管理变更详情", exact: true }),
  ).toBeVisible();
  await expect(
    page.getByRole("row").filter({ hasText: "核对时间" }),
  ).toContainText(/\d{4}\/\d{1,2}\/\d{1,2}/);
  await page.screenshot({
    path: "../.m5/audit-detail-desktop.png",
    fullPage: false,
    animations: "disabled",
  });
  await page.getByRole("button", { name: "关闭此对话框", exact: true }).click();
  await expect(page.locator(".el-drawer")).toBeHidden();
  await page.getByRole("link", { name: "用量与保护", exact: true }).click();
  await expect(page.locator(".usage-cards")).toContainText("累计估算");
  await expect(page.locator(".usage-cards")).toContainText("80 / ¥100");
  await page.screenshot({ path: "../.m5/usage-desktop.png", fullPage: true });
  await page.setViewportSize({ width: 390, height: 844 });
  await page.screenshot({ path: "../.m5/usage-mobile.png", fullPage: true });
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= window.innerWidth,
    ),
  ).toBeTruthy();
  const employee = await data(
    await request.post("/api/v1/auth/login", {
      headers: { Origin: origin },
      data: { username: "employee_li", password: credentials.employee_li },
    }),
  );
  const headers = { Authorization: "Bearer " + employee.access_token };
  expect(
    (await request.get("/api/v1/admin/audit-events", { headers })).status(),
  ).toBe(403);
  expect((await request.get("/api/v1/admin/usage", { headers })).status()).toBe(
    403,
  );
});
