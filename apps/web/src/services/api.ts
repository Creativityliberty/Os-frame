import type { MissionCreateOut, SSEEvent, ApprovalDecision } from "../shared/types";

const API_BASE = import.meta.env.VITE_API_BASE || "/api";

function getAccess(): string | null {
  return localStorage.getItem("AETHER_ACCESS_TOKEN");
}
function getRefresh(): string | null {
  return localStorage.getItem("AETHER_REFRESH_TOKEN");
}

function setTokens(access: string, refresh: string) {
  localStorage.setItem("AETHER_ACCESS_TOKEN", access);
  localStorage.setItem("AETHER_REFRESH_TOKEN", refresh);
}

export async function loginWithApiKey(api_key: string) {
  const r = await fetch(`${API_BASE}/auth/login`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ api_key }),
  });
  if (!r.ok) throw new Error(await r.text());
  const out = await r.json();
  setTokens(out.access_token, out.refresh_token);
  return out;
}

export async function refreshAccess() {
  const refresh_token = getRefresh();
  if (!refresh_token) throw new Error("no refresh token");
  const r = await fetch(`${API_BASE}/auth/refresh`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ refresh_token }),
  });
  if (!r.ok) throw new Error(await r.text());
  const out = await r.json();
  localStorage.setItem("AETHER_ACCESS_TOKEN", out.access_token);
  return out.access_token as string;
}

export async function logout() {
  const refresh_token = getRefresh();
  if (refresh_token) {
    await fetch(`${API_BASE}/auth/logout`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ refresh_token }),
    }).catch(() => {});
  }
  localStorage.removeItem("AETHER_ACCESS_TOKEN");
  localStorage.removeItem("AETHER_REFRESH_TOKEN");
}

async function authedFetch(input: RequestInfo, init?: RequestInit): Promise<Response> {
  const access = getAccess();
  const headers = { ...(init?.headers || {}), ...(access ? { Authorization: `Bearer ${access}` } : {}) } as any;
  const r1 = await fetch(input, { ...(init || {}), headers });
  if (r1.status !== 401) return r1;

  // try refresh once
  try {
    const newAccess = await refreshAccess();
    const headers2 = { ...(init?.headers || {}), Authorization: `Bearer ${newAccess}` } as any;
    return await fetch(input, { ...(init || {}), headers: headers2 });
  } catch {
    return r1;
  }
}

export async function me(): Promise<any> {
  const r = await authedFetch(`${API_BASE}/auth/me`);
  if (!r.ok) throw new Error(await r.text());
  return r.json();
}

export async function createMission(user_message: string, tenant_id?: string, title?: string, tags?: string[]): Promise<MissionCreateOut> {
  const r = await authedFetch(`${API_BASE}/missions`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ tenant_id, user_message, title, tags }),
  });
  if (!r.ok) throw new Error(await r.text());
  return r.json();
}

export function subscribeRun(run_id: string, onEvent: (ev: SSEEvent) => void, since_seq?: number) {
  const url = new URL(`${API_BASE}/runs/${run_id}/subscribe`);
  const tok = getAccess();
  if (tok) url.searchParams.set("access_token", tok);
  if (since_seq !== undefined) url.searchParams.set("since_seq", String(since_seq));

  const es = new EventSource(url.toString());
  es.onmessage = (msg) => {
    try {
      const ev = JSON.parse(msg.data);
      onEvent(ev);
    } catch {
      // ignore
    }
  };
  return () => es.close();
}

export async function approve(run_id: string, decision: ApprovalDecision, by = "human", reason?: string) {
  const r = await authedFetch(`${API_BASE}/runs/${run_id}/approve`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ decision, by, reason }),
  });
  if (!r.ok) throw new Error(await r.text());
  return r.json();
}

export async function fetchRegistry(): Promise<any> {
  const r = await authedFetch(`${API_BASE}/registry`);
  if (!r.ok) throw new Error(await r.text());
  return r.json();
}

export async function fetchRegistryEffective(): Promise<any> {
  const r = await authedFetch(`${API_BASE}/registry/effective`);
  if (!r.ok) throw new Error(await r.text());
  return r.json();
}

export async function saveRegistry(payload: any): Promise<any> {
  const r = await authedFetch(`${API_BASE}/registry`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  if (!r.ok) throw new Error(await r.text());
  return r.json();
}

export async function listMcpServers(): Promise<any> {
  const r = await authedFetch(`${API_BASE}/mcp/servers`);
  if (!r.ok) throw new Error(await r.text());
  return r.json();
}

export async function addMcpServer(server: { id: string; base_url: string; timeout_s?: number }): Promise<any> {
  const r = await authedFetch(`${API_BASE}/mcp/servers`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(server),
  });
  if (!r.ok) throw new Error(await r.text());
  return r.json();
}

export async function deleteMcpServer(id: string): Promise<any> {
  const r = await authedFetch(`${API_BASE}/mcp/servers/${encodeURIComponent(id)}`, { method: "DELETE" });
  if (!r.ok) throw new Error(await r.text());
  return r.json();
}

export async function discoverMcpTools(): Promise<any> {
  const r = await authedFetch(`${API_BASE}/mcp/discover`, { method: "POST" });
  if (!r.ok) throw new Error(await r.text());
  return r.json();
}

export async function listRuns(limit = 50, offset = 0, query?: string, state?: string, tag?: string): Promise<any> {
  const url = new URL(`${API_BASE}/runs`);
  url.searchParams.set("limit", String(limit));
  url.searchParams.set("offset", String(offset));
  if (query) url.searchParams.set("query", query);
  if (state) url.searchParams.set("state", state);
  if (tag) url.searchParams.set("tag", tag);
  const r = await authedFetch(url.toString());
  if (!r.ok) throw new Error(await r.text());
  return r.json();
}

export async function listApprovals(status: "pending" | "decided" = "pending"): Promise<any> {
  const url = new URL(`${API_BASE}/approvals`);
  url.searchParams.set("status", status);
  const r = await authedFetch(url.toString());
  if (!r.ok) throw new Error(await r.text());
  return r.json();
}

export async function exportRun(run_id: string): Promise<any> {
  const r = await authedFetch(`${API_BASE}/runs/${run_id}/export`);
  if (!r.ok) throw new Error(await r.text());
  return r.json();
}

export async function patchRun(run_id: string, title?: string, tags?: string[]): Promise<any> {
  const r = await authedFetch(`${API_BASE}/runs/${run_id}`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ title, tags }),
  });
  if (!r.ok) throw new Error(await r.text());
  return r.json();
}
