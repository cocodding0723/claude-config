#!/usr/bin/env node
// 운영툴 포털 MCP (stdio, 의존성 없음). 인증은 Tailscale 신원이 대신함 — 토큰 없음.
import { createInterface } from "node:readline";
import { pathToFileURL } from "node:url";

const BASE = process.env.OPS_PORTAL_BASE || "https://bs-portal.taile54389.ts.net/api/ops";
const DEFAULT_PROJECT = process.env.OPS_PORTAL_PROJECT || "lrm";
// 실행 버튼 대신 미리보기로 돌릴 짝. 매니페스트에 짝 정보가 없어 여기서 관리.
const PREVIEW_OF = { "key-request": "key-preview" };
const LIVE_BLOCKED = "라이브 작업은 MCP 에서 막혀 있음. 포털 화면에서 사용자가 직접 실행할 것";

export async function api(path, method = "GET", body) {
  const init = { method, headers: { Accept: "application/json" } };
  if (method !== "GET") {
    // 포털이 쓰기 요청에 요구하는 헤더. Origin 없으면 403(cross-origin write refused)
    Object.assign(init.headers, { "X-Portal-Ops": "1", "Content-Type": "application/json", Origin: new URL(BASE).origin });
    init.body = JSON.stringify(body || {});
  }
  const r = await fetch(BASE + path, init);
  const text = await r.text();
  let json;
  try { json = JSON.parse(text); } catch { json = { raw: text.slice(0, 2000) }; }
  if (!r.ok && r.status !== 202) throw new Error(`포털 ${method} ${path} → ${r.status}: ${JSON.stringify(json).slice(0, 500)}`);
  return json;
}

const enc = encodeURIComponent;
const actionsOf = (manifest) => (manifest.features || []).flatMap((f) => f.actions || []);

// MCP 실행 허용 조건: 데브 역할(user)이고 위험 표시 없음. 라이브 역할이 새로 생겨도 자동으로 막힘.
export function checkRunnable(action) {
  if (!action) return "매니페스트에 없는 액션";
  if (action.role !== "user" || action.danger) return `${action.id}: ${LIVE_BLOCKED}`;
  return null;
}

export function resolveRun(manifest, { action, text, dryRun }) {
  const find = (id) => actionsOf(manifest).find((a) => a.id === id);
  const base = find(action);
  if (!base) return { error: `${action}: 매니페스트에 없는 액션` };
  const baseErr = checkRunnable(base);
  if (baseErr) return { error: baseErr };
  const target = dryRun ? (base.dryRun ? base : find(PREVIEW_OF[action])) : base;
  if (!target) return { error: `${action}: 미리보기 짝이 없음. dryRun 은 ${Object.keys(PREVIEW_OF).join(", ")} 에만 가능` };
  const err = checkRunnable(target);
  if (err) return { error: err };
  const body = { action: target.id };
  if (target.input === "text") {
    if (!text?.trim()) return { error: `${target.id}: text 필요 — ${target.help || ""}` };
    body.text = text.trim();
  }
  return { body, target };
}

const P = { type: "string", description: `프로젝트 id. 기본 ${DEFAULT_PROJECT}` };
export const TOOLS = [
  { name: "ops_projects", description: "내 계정·역할과 접근 가능한 프로젝트 목록 (읽기 전용)", inputSchema: { type: "object", properties: {} },
    run: async () => ({ me: await api("/me"), projects: await api("/projects") }) },
  { name: "ops_manifest", description: "프로젝트의 기능·액션·역할·패널 정의 (읽기 전용). 각 액션의 role·danger·help 로 부작용 확인",
    inputSchema: { type: "object", properties: { project: P } },
    run: async ({ project = DEFAULT_PROJECT }) => {
      const m = await api(`/p/${enc(project)}/manifest`);
      return { ...m, actions: actionsOf(m).map((a) => ({ id: a.id, label: a.label, role: a.role, danger: !!a.danger, input: a.input || null, mcp: checkRunnable(a) ? "막힘(포털 전용)" : "실행 가능", help: a.help })) };
    } },
  { name: "ops_panel", description: "패널 조회 (읽기 전용): sprint(스프린트)·patches(패치 적용 현황)·versions(버전별 데이터 목록)",
    inputSchema: { type: "object", properties: { project: P, panel: { type: "string", enum: ["sprint", "patches", "versions"] }, query: { type: "string", description: "선택. 쿼리 문자열(? 없이)" } }, required: ["panel"] },
    run: ({ project = DEFAULT_PROJECT, panel, query }) => api(`/p/${enc(project)}/panels/${enc(panel)}${query ? "?" + query : ""}`) },
  { name: "ops_jobs", description: "최근 작업 목록 (읽기 전용). since 이후 이벤트 번호만",
    inputSchema: { type: "object", properties: { project: P, since: { type: "number", description: "기본 0" }, limit: { type: "number", description: "돌려줄 최대 개수. 기본 20" } } },
    run: async ({ project = DEFAULT_PROJECT, since = 0, limit = 20 }) => {
      const r = await api(`/p/${enc(project)}/jobs?since=${since}`);
      return { seq: r.seq, jobs: (r.jobs || []).slice(0, limit).map(({ jobId, action, label, status, class: cls, reason, requester, created, run }) => ({ jobId, action, label, status, class: cls, reason, requester: requester?.name, created, runUrl: run?.url })) };
    } },
  { name: "ops_job", description: "작업 하나의 상태·대기 항목·로그 (읽기 전용)",
    inputSchema: { type: "object", properties: { project: P, jobId: { type: "string" } }, required: ["jobId"] },
    run: ({ project = DEFAULT_PROJECT, jobId }) => api(`/p/${enc(project)}/jobs/${enc(jobId)}`) },
  { name: "ops_audit", description: "감사 기록 조회 (읽기 전용)",
    inputSchema: { type: "object", properties: { query: { type: "string", description: "선택. 포털 감사 화면과 같은 쿼리 문자열(? 없이). 예: limit=50" } } },
    run: ({ query = "limit=50" }) => api(`/audit?${query}`) },
  { name: "ops_run",
    description: "데브 액션 실행. 부작용 있음: key-request·fill 은 데브 시트에 바로 씀, locale-dev·data-dev·scenario-dev 는 데브 서버에 바로 올림, data-sync 는 저장소에 바로 반영 후 데브 업로드까지 이어짐. 라이브 역할·위험 액션은 거부됨(포털에서 직접). dryRun=true 면 미리보기 짝(key-request→key-preview)으로 대신 실행해 시트를 안 씀. 시트에 쓰는 액션은 먼저 dryRun 으로 확인할 것. 반환된 jobId 는 ops_job 으로 확인",
    inputSchema: { type: "object", properties: { project: P, action: { type: "string" }, text: { type: "string", description: "input=text 액션의 입력 문장" }, dryRun: { type: "boolean" } }, required: ["action"] },
    run: async ({ project = DEFAULT_PROJECT, action, text, dryRun = false }) => {
      const m = await api(`/p/${enc(project)}/manifest`);
      const { error, body, target } = resolveRun(m, { action, text, dryRun });
      if (error) throw new Error(error);
      const r = await api(`/p/${enc(project)}/jobs`, "POST", body);
      return { ran: target.id, dryRun: !!target.dryRun, jobId: r.jobId, next: "ops_job 으로 상태 확인" };
    } },
];

async function handle(msg) {
  const { id, method, params } = msg;
  if (method === "initialize") return { protocolVersion: params?.protocolVersion || "2025-06-18", capabilities: { tools: {} }, serverInfo: { name: "ops-portal", version: "0.1.0" } };
  if (method === "tools/list") return { tools: TOOLS.map(({ run, ...t }) => t) };
  if (method === "tools/call") {
    const tool = TOOLS.find((t) => t.name === params.name);
    if (!tool) return { content: [{ type: "text", text: `없는 도구: ${params.name}` }], isError: true };
    try {
      return { content: [{ type: "text", text: JSON.stringify(await tool.run(params.arguments || {}), null, 1) }] };
    } catch (e) {
      return { content: [{ type: "text", text: String(e.message || e) }], isError: true };
    }
  }
  if (method === "ping") return {};
  if (id === undefined) return undefined; // 알림은 응답 없음
  throw Object.assign(new Error(`지원 안 함: ${method}`), { code: -32601 });
}

if (import.meta.url === pathToFileURL(process.argv[1]).href) {
  createInterface({ input: process.stdin }).on("line", async (line) => {
    if (!line.trim()) return;
    let msg;
    try { msg = JSON.parse(line); } catch { return; }
    try {
      const result = await handle(msg);
      if (msg.id !== undefined && result !== undefined) process.stdout.write(JSON.stringify({ jsonrpc: "2.0", id: msg.id, result }) + "\n");
    } catch (e) {
      if (msg.id !== undefined) process.stdout.write(JSON.stringify({ jsonrpc: "2.0", id: msg.id, error: { code: e.code || -32603, message: e.message } }) + "\n");
    }
  });
}
