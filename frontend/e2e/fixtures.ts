import { test as base, expect } from "@playwright/test";
import type { APIRequestContext } from "@playwright/test";
import { existsSync, readFileSync, writeFileSync, unlinkSync } from "node:fs";
const checkpoint = new URL(
  "../../.m5/membership-checkpoint.json",
  import.meta.url,
);
const credentials = JSON.parse(
  readFileSync(
    new URL("../../data/runtime/demo-credentials.json", import.meta.url),
    "utf8",
  ),
) as Record<string, string>;
export function checkpointMembership(
  kbId: string,
  before: string[],
  after: string[],
) {
  writeFileSync(
    checkpoint,
    JSON.stringify({ kb_id: kbId, before, after }, null, 2),
  );
}
async function restoreMembership(request: APIRequestContext) {
  if (!existsSync(checkpoint)) return;
  const state = JSON.parse(readFileSync(checkpoint, "utf8")) as {
    kb_id: string;
    before: string[];
    after: string[];
  };
  if (
    !/^[a-f0-9-]{36}$/.test(state.kb_id) ||
    ![state.before, state.after].every(
      (v) => Array.isArray(v) && v.every((id) => /^[a-f0-9-]{36}$/.test(id)),
    )
  )
    throw new Error("Invalid test authorization checkpoint");
  const login = await request.post("/api/v1/auth/login", {
    headers: { Origin: "http://127.0.0.1:5173" },
    data: { username: "admin", password: credentials.admin },
    timeout: 10000,
  });
  expect(login.status()).toBe(200);
  const headers = {
    Authorization: "Bearer " + (await login.json()).data.access_token,
  };
  const endpoint = `/api/v1/admin/knowledge-bases/${state.kb_id}/members`;
  const response = await request.get(endpoint, { headers, timeout: 10000 });
  expect(response.status()).toBe(200);
  const current = (await response.json()).data;
  const same = (a: string[], b: string[]) =>
    JSON.stringify([...a].sort()) === JSON.stringify([...b].sort());
  if (!same(current.user_ids, state.before)) {
    if (!same(current.user_ids, state.after))
      throw new Error(
        "Authorization changed outside this test checkpoint; refusing to overwrite it",
      );
    const restored = await request.put(endpoint, {
      headers,
      data: { expected_revision: current.revision, user_ids: state.before },
      timeout: 10000,
    });
    expect(restored.status()).toBe(200);
  }
  unlinkSync(checkpoint);
}
export const test = base.extend<{ _restoreTestMembership: void }>({
  _restoreTestMembership: [
    async ({ request }, use) => {
      await restoreMembership(request);
      try {
        await use();
      } finally {
        await restoreMembership(request);
      }
    },
    { auto: true },
  ],
});
export { expect };
