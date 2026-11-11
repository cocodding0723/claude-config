// 실행 허용 판정 시험. 포털 호출 없음.
import { test } from "node:test";
import assert from "node:assert/strict";
import { resolveRun, summarizeRunners, summarizeClaude, TOOLS } from "./server.mjs";

const manifest = { features: [{ actions: [
  { id: "key-request", role: "user", input: "text" },
  { id: "key-preview", role: "user", input: "text", dryRun: true },
  { id: "data-dev", role: "user" },
  { id: "locale-live", role: "local-live", danger: true },
  { id: "odd-danger", role: "user", danger: true },
] }] };

test("데브 액션 허용", () => assert.deepEqual(resolveRun(manifest, { action: "data-dev" }).body, { action: "data-dev" }));
test("라이브 역할 거부", () => assert.match(resolveRun(manifest, { action: "locale-live" }).error, /포털 화면/));
test("위험 표시 거부", () => assert.match(resolveRun(manifest, { action: "odd-danger" }).error, /포털 화면/));
test("없는 액션 거부", () => assert.match(resolveRun(manifest, { action: "nope" }).error, /없는 액션/));
test("dryRun 은 미리보기 짝으로", () => assert.deepEqual(resolveRun(manifest, { action: "key-request", text: "a", dryRun: true }).body, { action: "key-preview", text: "a" }));
test("짝 없는 dryRun 거부", () => assert.match(resolveRun(manifest, { action: "data-dev", dryRun: true }).error, /미리보기 짝/));
test("text 필수", () => assert.match(resolveRun(manifest, { action: "key-request" }).error, /text 필요/));

const raw = { now: "t", groups: { light: { total: 3, busy: 1, queued: 0 } },
  runners: [{ name: "m1", status: "online", busy: true, group: "light", labels: ["x"] }, { name: "m2", status: "offline", busy: false, group: "heavy" }],
  running: [{ repo: "r", workflow: "w", job: "j", runner: "m1", group: "light", url: "u", jobId: 1 }], queued: [],
  claude: { account: { state: "blocked", resetsAt: "z" }, backlog: 3 }, meta: { stale: false, runnersUpdatedAt: "a", jobsUpdatedAt: "b", errors: {} } };
test("러너 요약: 오프라인·실행 중", () => {
  const s = summarizeRunners(raw);
  assert.deepEqual(s.offline, ["m2"]);
  assert.equal(s.running[0].runner, "m1");
  assert.equal(s.runners[0].labels, undefined);
});
test("Claude 계정 요약: ok 아니면 blocked", () => {
  assert.equal(summarizeClaude(raw).blocked, true);
  assert.equal(summarizeClaude({ claude: { account: { state: "ok" } } }).blocked, false);
});
test("bump 도구 없음(부작용)", () => assert.ok(!TOOLS.some((t) => /bump/.test(t.name))));
