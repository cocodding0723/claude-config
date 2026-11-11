// 실행 허용 판정 시험. 포털 호출 없음.
import { test } from "node:test";
import assert from "node:assert/strict";
import { resolveRun } from "./server.mjs";

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
