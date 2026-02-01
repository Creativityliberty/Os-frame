import React, { useEffect, useMemo, useRef, useState } from "react";
import type { SSEEvent } from "../shared/types";
import {
  approve,
  createMission,
  subscribeRun,
  fetchRegistry,
  fetchRegistryEffective,
  saveRegistry,
  listMcpServers,
  addMcpServer,
  deleteMcpServer,
  discoverMcpTools,
  loginWithApiKey,
  me,
  listRuns,
  listApprovals,
  exportRun,
  patchRun,
  logout,
} from "../services/api";

type Tab = "runs" | "run" | "artifacts" | "dag" | "registry" | "mcp";

function Button(props: React.ButtonHTMLAttributes<HTMLButtonElement>) {
  return (
    <button
      {...props}
      style={{
        padding: "8px 10px",
        borderRadius: 10,
        border: "1px solid rgba(255,255,255,0.15)",
        background: "rgba(255,255,255,0.06)",
        color: "white",
        cursor: "pointer",
        ...(props.style || {}),
      }}
    />
  );
}

function Input(props: React.InputHTMLAttributes<HTMLInputElement>) {
  return (
    <input
      {...props}
      style={{
        padding: "8px 10px",
        borderRadius: 10,
        border: "1px solid rgba(255,255,255,0.15)",
        background: "rgba(255,255,255,0.06)",
        color: "white",
        width: "100%",
        ...(props.style || {}),
      }}
    />
  );
}

function TextArea(props: React.TextareaHTMLAttributes<HTMLTextAreaElement>) {
  return (
    <textarea
      {...props}
      style={{
        padding: "10px",
        borderRadius: 10,
        border: "1px solid rgba(255,255,255,0.15)",
        background: "rgba(255,255,255,0.06)",
        color: "white",
        width: "100%",
        ...(props.style || {}),
      }}
    />
  );
}

function Card({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <div style={{ border: "1px solid rgba(255,255,255,0.12)", borderRadius: 16, padding: 12, background: "rgba(255,255,255,0.04)" }}>
      <div style={{ fontWeight: 800, marginBottom: 8 }}>{title}</div>
      {children}
    </div>
  );
}

function extractArtifacts(events: SSEEvent[]) {
  const artifacts: Array<{ seq?: number; kind: string; value: any }> = [];
  for (const ev of events) {
    if (ev.type === "TaskArtifactUpdateEvent") {
      artifacts.push({ seq: ev._seq, kind: ev.artifact_type || "artifact", value: ev.artifact });
    }
  }
  return artifacts;
}

function extractPlan(events: SSEEvent[]) {
  const arts = extractArtifacts(events);
  for (let i = arts.length - 1; i >= 0; i--) if (arts[i].kind === "plan") return arts[i].value;
  return null;
}

function extractStepResults(events: SSEEvent[]) {
  return extractArtifacts(events).filter(a => a.kind === "step_result").map(a => a.value);
}

function DagView({ plan, stepResults }: { plan: any; stepResults: any[] }) {
  const nodes = (plan?.steps || []).map((s: any) => ({ id: s.step_id, label: `${s.step_id}: ${s.action_id}`, depends: s.depends_on || [] }));
  const statusByStep = new Map<string, string>();
  for (const sr of stepResults) statusByStep.set(sr.step_id, sr.status);

  const depth = new Map<string, number>();
  function getDepth(id: string): number {
    if (depth.has(id)) return depth.get(id)!;
    const n = nodes.find(n => n.id === id);
    if (!n) return 0;
    const d = (n.depends || []).reduce((mx: number, depId: string) => Math.max(mx, getDepth(depId) + 1), 0);
    depth.set(id, d);
    return d;
  }
  for (const n of nodes) getDepth(n.id);

  const layers = new Map<number, typeof nodes>();
  for (const n of nodes) {
    const d = depth.get(n.id) || 0;
    layers.set(d, [...(layers.get(d) || []), n]);
  }

  const layerKeys = Array.from(layers.keys()).sort((a, b) => a - b);
  const positions = new Map<string, { x: number; y: number }>();
  const xGap = 260;
  const yGap = 90;

  layerKeys.forEach((d, i) => {
    const arr = layers.get(d)!;
    arr.forEach((n, j) => positions.set(n.id, { x: 30 + i * xGap, y: 30 + j * yGap }));
  });

  const width = 30 + layerKeys.length * xGap + 260;
  const height = 30 + Math.max(...layerKeys.map(d => layers.get(d)!.length)) * yGap + 140;

  return (
    <svg width="100%" viewBox={`0 0 ${width} ${height}`} style={{ background: "rgba(0,0,0,0.2)", borderRadius: 16, border: "1px solid rgba(255,255,255,0.12)" }}>
      {nodes.flatMap(n => (n.depends || []).map((depId: string) => {
        const a = positions.get(depId);
        const b = positions.get(n.id);
        if (!a || !b) return null;
        const x1 = a.x + 220;
        const y1 = a.y + 18;
        const x2 = b.x;
        const y2 = b.y + 18;
        return <path key={`${depId}->${n.id}`} d={`M ${x1} ${y1} C ${x1 + 50} ${y1}, ${x2 - 50} ${y2}, ${x2} ${y2}`} stroke="rgba(255,255,255,0.35)" fill="none" />;
      }))}
      {nodes.map(n => {
        const p = positions.get(n.id)!;
        const st = statusByStep.get(n.id) || "pending";
        const stroke = st === "succeeded" ? "rgba(0,255,140,0.7)" : st === "failed" ? "rgba(255,80,80,0.8)" : "rgba(255,255,255,0.25)";
        return (
          <g key={n.id} transform={`translate(${p.x},${p.y})`}>
            <rect x={0} y={0} rx={12} ry={12} width={220} height={36} fill="rgba(255,255,255,0.05)" stroke={stroke} />
            <text x={10} y={23} fill="white" fontSize="12" fontFamily="system-ui">{n.label}</text>
            <text x={180} y={23} fill="rgba(255,255,255,0.75)" fontSize="11" fontFamily="system-ui">{st}</text>
          </g>
        );
      })}
    </svg>
  );
}

function Login({ onOk }: { onOk: () => void }) {
  const [k, setK] = useState("dev_admin_key");
  const [status, setStatus] = useState<string>("");

  async function doLogin() {
    setStatus("logging in…");
    try {
      const out = await loginWithApiKey(k);
      setStatus(`ok: ${out.user.user_id} (${out.user.tenant_id}) roles=${(out.user.roles || []).join(",")}`);
      onOk();
    } catch (e: any) {
      setStatus("error: " + e.message);
    }
  }

  return (
    <Card title="Login (API key → JWT session)">
      <div style={{ fontSize: 12, opacity: 0.8, marginBottom: 6 }}>
        Tu log avec une <code>api_key</code> (dev), le serveur te renvoie <code>access_token</code> + <code>refresh_token</code>.
      </div>
      <Input value={k} onChange={(e) => setK(e.target.value)} placeholder="dev_admin_key" />
      <div style={{ marginTop: 10, display: "flex", gap: 8 }}>
        <Button onClick={doLogin}>Login</Button>
      </div>
      <div style={{ marginTop: 8, fontSize: 12, opacity: 0.8 }}>{status}</div>
    </Card>
  );
}

export default function App() {
  const [tab, setTab] = useState<Tab>("runs");
  const [user, setUser] = useState<any>(null);

  // mission
  const [goal, setGoal] = useState("Crée un plan support pour un remboursement et rédige la réponse.");
  const [title, setTitle] = useState("Refund response");
  const [tags, setTags] = useState("support,refund");
  const [runId, setRunId] = useState<string | null>(null);
  const [events, setEvents] = useState<SSEEvent[]>([]);
  const lastSeq = useRef<number>(0);
  const [needsApproval, setNeedsApproval] = useState(false);

  // projections
  const [runs, setRuns] = useState<any[]>([]);
  const [approvals, setApprovals] = useState<any[]>([]);
  const [runsStatus, setRunsStatus] = useState<string>("");

  // runs filters
  const [q, setQ] = useState("");
  const [state, setState] = useState("");
  const [tag, setTag] = useState("");

  // registry editor
  const [registryText, setRegistryText] = useState<string>("");
  const [registryEffectiveText, setRegistryEffectiveText] = useState<string>("");
  const [registryStatus, setRegistryStatus] = useState<string>("");

  // MCP
  const [mcpServers, setMcpServers] = useState<Array<any>>([]);
  const [mcpId, setMcpId] = useState("calendar");
  const [mcpUrl, setMcpUrl] = useState("http://localhost:9100");
  const [mcpDiscoverResult, setMcpDiscoverResult] = useState<any>(null);

  const plan = useMemo(() => extractPlan(events), [events]);
  const stepResults = useMemo(() => extractStepResults(events), [events]);
  const artifacts = useMemo(() => extractArtifacts(events), [events]);

  async function refreshUser() {
    try {
      const u = await me();
      setUser(u);
    } catch {
      setUser(null);
    }
  }

  useEffect(() => {
    refreshUser();
  }, []);

  async function refreshRuns() {
    setRunsStatus("loading…");
    try {
      const r = await listRuns(50, 0, q || undefined, state || undefined, tag || undefined);
      setRuns(r.runs || []);
      const a = await listApprovals("pending");
      setApprovals(a.approvals || []);
      setRunsStatus("loaded");
    } catch (e: any) {
      setRunsStatus("error: " + e.message);
    }
  }

  async function start() {
    setEvents([]);
    setNeedsApproval(false);
    const out = await createMission(goal, undefined, title || undefined, tags ? tags.split(",").map(s => s.trim()).filter(Boolean) : undefined);
    setRunId(out.run_id);

    const stop = subscribeRun(out.run_id, (ev) => {
      if (typeof (ev as any)._seq === "number") lastSeq.current = (ev as any)._seq;
      setEvents((prev) => [...prev, ev]);
      if (ev.type === "TaskStatusUpdateEvent" && (ev as any).state === "input-required") setNeedsApproval(true);
    });

    (window as any).__stopSSE = stop;
    setTab("run");
    refreshRuns();
  }

  async function selectRun(rid: string) {
    if ((window as any).__stopSSE) (window as any).__stopSSE();
    setEvents([]);
    setNeedsApproval(false);
    setRunId(rid);

    const stop = subscribeRun(rid, (ev) => {
      if (typeof (ev as any)._seq === "number") lastSeq.current = (ev as any)._seq;
      setEvents((prev) => [...prev, ev]);
      if (ev.type === "TaskStatusUpdateEvent" && (ev as any).state === "input-required") setNeedsApproval(true);
    }, 0);

    (window as any).__stopSSE = stop;
    setTab("run");
  }

  async function doApprove(decision: "approved" | "denied") {
    if (!runId) return;
    await approve(runId, decision);
    setNeedsApproval(false);
    refreshRuns();
  }

  async function loadRegistry() {
    setRegistryStatus("loading…");
    try {
      const reg = await fetchRegistry();
      setRegistryText(JSON.stringify(reg, null, 2));
      const eff = await fetchRegistryEffective();
      setRegistryEffectiveText(JSON.stringify(eff, null, 2));
      setRegistryStatus("loaded");
    } catch (e: any) {
      setRegistryStatus("error: " + e.message);
    }
  }

  async function saveRegistryClicked() {
    setRegistryStatus("saving…");
    try {
      const payload = JSON.parse(registryText);
      await saveRegistry(payload);
      setRegistryStatus("saved ✅");
    } catch (e: any) {
      setRegistryStatus("error: " + e.message);
    }
  }

  async function loadMcpServers() {
    const res = await listMcpServers();
    setMcpServers(res.servers || []);
  }

  async function addMcp() {
    await addMcpServer({ id: mcpId, base_url: mcpUrl, timeout_s: 15 });
    await loadMcpServers();
  }

  async function delMcp(id: string) {
    await deleteMcpServer(id);
    await loadMcpServers();
  }

  async function discover() {
    const r = await discoverMcpTools();
    setMcpDiscoverResult(r);
  }

  async function doExport() {
    if (!runId) return;
    const bundle = await exportRun(runId);
    alert("export loaded (see console)");
    console.log("EXPORT", bundle);
  }

  async function doPatchMeta() {
    if (!runId) return;
    await patchRun(runId, title || undefined, tags ? tags.split(",").map(s => s.trim()).filter(Boolean) : undefined);
    refreshRuns();
  }

  async function doLogout() {
    await logout();
    setUser(null);
  }

  const tabs: Tab[] = ["runs", "run", "artifacts", "dag", "registry", "mcp"];

  return (
    <div style={{ fontFamily: "system-ui", color: "white", minHeight: "100vh", background: "radial-gradient(circle at 20% 10%, rgba(0,200,255,0.20), transparent 50%), radial-gradient(circle at 80% 0%, rgba(255,0,120,0.18), transparent 45%), #0b0d12" }}>
      <div style={{ maxWidth: 1180, margin: "0 auto", padding: 18 }}>
        <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", gap: 12 }}>
          <div>
            <div style={{ fontSize: 22, fontWeight: 900 }}>Aether Cockpit — Enterprise+</div>
            <div style={{ opacity: 0.75, fontSize: 13 }}>
              JWT Sessions • Registry-driven RBAC • Layered Registry • Budgets (tool/llm/cost) • Search/Tags/Export • DAG • MCP
            </div>
          </div>
          <div style={{ display: "flex", gap: 10, alignItems: "center", fontSize: 12, opacity: 0.85 }}>
            <div><b>user:</b> {user ? user.user_id : "-"}</div>
            <div><b>tenant:</b> {user ? user.tenant_id : "-"}</div>
            <div><b>roles:</b> {user ? (user.roles || []).join(",") : "-"}</div>
            <div style={{ display: "flex", gap: 8 }}>
              <Button onClick={refreshUser}>Refresh</Button>
              <Button onClick={doLogout}>Logout</Button>
            </div>
          </div>
        </div>

        <div style={{ marginTop: 14, display: "flex", gap: 8, flexWrap: "wrap" }}>
          {tabs.map(t => (
            <Button key={t} onClick={() => setTab(t)} style={{ background: tab === t ? "rgba(255,255,255,0.16)" : "rgba(255,255,255,0.06)" }}>
              {t.toUpperCase()}
            </Button>
          ))}
        </div>

        <div style={{ marginTop: 14 }}>
          {!user && <Login onOk={() => refreshUser()} />}

          {user && tab === "runs" && (
            <div style={{ display: "grid", gridTemplateColumns: "1.1fr 0.9fr", gap: 12 }}>
              <Card title="Runs (Projection + Search)">
                <div style={{ display: "grid", gridTemplateColumns: "1.4fr 0.7fr 0.7fr 0.7fr", gap: 8, marginBottom: 10 }}>
                  <Input value={q} onChange={(e) => setQ(e.target.value)} placeholder="query (task_id/title/JSON)" />
                  <Input value={state} onChange={(e) => setState(e.target.value)} placeholder="state" />
                  <Input value={tag} onChange={(e) => setTag(e.target.value)} placeholder="tag" />
                  <Button onClick={refreshRuns}>Search</Button>
                </div>

                <div style={{ display: "flex", gap: 8, marginBottom: 10 }}>
                  <Button onClick={refreshRuns}>Refresh</Button>
                  <Button onClick={start}>New Mission</Button>
                  <div style={{ fontSize: 12, opacity: 0.8 }}>{runsStatus}</div>
                </div>

                <div style={{ display: "flex", flexDirection: "column", gap: 8, maxHeight: 420, overflow: "auto" }}>
                  {(runs || []).map(r => (
                    <div key={r.run_id} style={{ display: "flex", justifyContent: "space-between", gap: 8, padding: 10, borderRadius: 12, border: "1px solid rgba(255,255,255,0.12)", background: "rgba(255,255,255,0.03)" }}>
                      <div style={{ overflow: "hidden" }}>
                        <div style={{ fontWeight: 800 }}>{r.state} • {r.run_id}</div>
                        <div style={{ opacity: 0.75, fontSize: 12 }}>task_id: {r.task_id}</div>
                        <div style={{ opacity: 0.75, fontSize: 12 }}>title: {r.title || "-"}</div>
                        <div style={{ opacity: 0.75, fontSize: 12 }}>tags: {(r.tags || []).join(",")}</div>
                        <div style={{ opacity: 0.75, fontSize: 12 }}>budget: {r.budget_used ? JSON.stringify(r.budget_used) : "-"}</div>
                      </div>
                      <Button onClick={() => selectRun(r.run_id)}>Open</Button>
                    </div>
                  ))}
                </div>
              </Card>

              <Card title="Approvals (Pending)">
                <div style={{ fontSize: 12, opacity: 0.8, marginBottom: 10 }}>
                  Queue de gating: quand un run passe en <code>input-required</code>.
                </div>
                <div style={{ display: "flex", flexDirection: "column", gap: 8, maxHeight: 420, overflow: "auto" }}>
                  {(approvals || []).length === 0 ? (
                    <div style={{ opacity: 0.75 }}>No pending approvals.</div>
                  ) : approvals.map(a => (
                    <div key={a.approval_id} style={{ padding: 10, borderRadius: 12, border: "1px solid rgba(255,255,255,0.12)", background: "rgba(255,255,255,0.03)" }}>
                      <div style={{ fontWeight: 800 }}>{a.approval_id}</div>
                      <div style={{ opacity: 0.75, fontSize: 12 }}>run: {a.run_id}</div>
                      <div style={{ marginTop: 8, display: "flex", gap: 8 }}>
                        <Button onClick={() => selectRun(a.run_id)}>Open</Button>
                      </div>
                    </div>
                  ))}
                </div>
              </Card>
            </div>
          )}

          {user && tab === "run" && (
            <div style={{ display: "grid", gridTemplateColumns: "1.1fr 0.9fr", gap: 12 }}>
              <Card title="Mission">
                <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 8, marginBottom: 8 }}>
                  <Input value={title} onChange={(e) => setTitle(e.target.value)} placeholder="title" />
                  <Input value={tags} onChange={(e) => setTags(e.target.value)} placeholder="tags (comma)" />
                </div>

                <TextArea rows={6} value={goal} onChange={(e) => setGoal(e.target.value)} />
                <div style={{ marginTop: 10, display: "flex", gap: 8, alignItems: "center", flexWrap: "wrap" }}>
                  <Button onClick={start}>Run</Button>
                  <Button onClick={doPatchMeta}>Save Meta</Button>
                  <Button onClick={doExport}>Export</Button>
                  <Button onClick={refreshRuns}>Refresh Runs</Button>
                  {needsApproval && (
                    <>
                      <Button onClick={() => doApprove("approved")} style={{ borderColor: "rgba(0,255,140,0.35)" }}>Approve</Button>
                      <Button onClick={() => doApprove("denied")} style={{ borderColor: "rgba(255,80,80,0.45)" }}>Deny</Button>
                    </>
                  )}
                </div>
                <div style={{ marginTop: 10, fontSize: 12, opacity: 0.8 }}>
                  <div><b>run_id:</b> {runId || "-"}</div>
                  <div><b>last_seq:</b> {lastSeq.current || 0}</div>
                  <div>LLM budgets: select_nodes + build_plan consomment des cost units.</div>
                </div>
              </Card>

              <Card title="Live SSE events (last 50)">
                <pre style={{ whiteSpace: "pre-wrap", background: "rgba(0,0,0,0.35)", padding: 12, borderRadius: 12, border: "1px solid rgba(255,255,255,0.10)", height: 360, overflow: "auto" }}>
                  {JSON.stringify(events.slice(-50), null, 2)}
                </pre>
              </Card>
            </div>
          )}

          {user && tab === "artifacts" && (
            <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 12 }}>
              <Card title={`Plan (${plan ? "found" : "none"})`}>
                <pre style={{ whiteSpace: "pre-wrap", background: "rgba(0,0,0,0.35)", padding: 12, borderRadius: 12, border: "1px solid rgba(255,255,255,0.10)", height: 360, overflow: "auto" }}>
                  {plan ? JSON.stringify(plan, null, 2) : "No plan artifact yet."}
                </pre>
              </Card>
              <Card title="Step Results">
                <pre style={{ whiteSpace: "pre-wrap", background: "rgba(0,0,0,0.35)", padding: 12, borderRadius: 12, border: "1px solid rgba(255,255,255,0.10)", height: 360, overflow: "auto" }}>
                  {stepResults.length ? JSON.stringify(stepResults, null, 2) : "No step results yet."}
                </pre>
              </Card>

              <Card title={`All artifacts (${artifacts.length})`}>
                <pre style={{ whiteSpace: "pre-wrap", background: "rgba(0,0,0,0.35)", padding: 12, borderRadius: 12, border: "1px solid rgba(255,255,255,0.10)", height: 260, overflow: "auto" }}>
                  {JSON.stringify(artifacts, null, 2)}
                </pre>
              </Card>

              <Card title="Gate / Approvals">
                <div style={{ fontSize: 13, opacity: 0.85 }}>
                  Si le kernel passe en <code>input-required</code>, approve/deny dans RUN.
                </div>
              </Card>
            </div>
          )}

          {user && tab === "dag" && (
            <div style={{ display: "grid", gridTemplateColumns: "1fr", gap: 12 }}>
              <Card title="DAG Viewer (from plan.steps + depends_on)">
                {plan ? <DagView plan={plan} stepResults={stepResults} /> : <div style={{ opacity: 0.8 }}>Aucun plan. Lance une mission d’abord.</div>}
              </Card>
            </div>
          )}

          {user && tab === "registry" && (
            <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 12 }}>
              <Card title="Registry Editor (base)">
                <div style={{ display: "flex", gap: 8, alignItems: "center", marginBottom: 10 }}>
                  <Button onClick={loadRegistry}>Load</Button>
                  <Button onClick={saveRegistryClicked}>Save</Button>
                  <div style={{ fontSize: 12, opacity: 0.8 }}>{registryStatus}</div>
                </div>
                <TextArea rows={18} value={registryText} onChange={(e) => setRegistryText(e.target.value)} />
                <div style={{ marginTop: 8, fontSize: 12, opacity: 0.75 }}>
                  Effective registry is merged with org/tenant/user override files.
                </div>
              </Card>

              <Card title="Effective Registry (merged layers)">
                <TextArea rows={18} value={registryEffectiveText} readOnly />
              </Card>
            </div>
          )}

          {user && tab === "mcp" && (
            <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 12 }}>
              <Card title="MCP Servers (capability: mcp:manage)">
                <div style={{ display: "flex", gap: 8, marginBottom: 10 }}>
                  <Button onClick={loadMcpServers}>Refresh</Button>
                  <Button onClick={discover}>Discover Tools</Button>
                </div>

                <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 8 }}>
                  <div>
                    <div style={{ fontSize: 12, opacity: 0.8, marginBottom: 4 }}>id</div>
                    <Input value={mcpId} onChange={(e) => setMcpId(e.target.value)} />
                  </div>
                  <div>
                    <div style={{ fontSize: 12, opacity: 0.8, marginBottom: 4 }}>base_url</div>
                    <Input value={mcpUrl} onChange={(e) => setMcpUrl(e.target.value)} />
                  </div>
                </div>
                <div style={{ marginTop: 10 }}>
                  <Button onClick={addMcp}>Add / Update</Button>
                </div>

                <div style={{ marginTop: 12 }}>
                  {(mcpServers || []).length === 0 ? (
                    <div style={{ opacity: 0.75 }}>No servers configured yet.</div>
                  ) : (
                    <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
                      {mcpServers.map((s) => (
                        <div key={s.id} style={{ display: "flex", justifyContent: "space-between", gap: 8, padding: 10, borderRadius: 12, border: "1px solid rgba(255,255,255,0.12)", background: "rgba(255,255,255,0.03)" }}>
                          <div>
                            <div style={{ fontWeight: 700 }}>{s.id}</div>
                            <div style={{ opacity: 0.8, fontSize: 12 }}>{s.base_url}</div>
                          </div>
                          <Button onClick={() => delMcp(s.id)} style={{ borderColor: "rgba(255,80,80,0.45)" }}>Delete</Button>
                        </div>
                      ))}
                    </div>
                  )}
                </div>
              </Card>

              <Card title="Discovery Results (/tools)">
                <pre style={{ whiteSpace: "pre-wrap", background: "rgba(0,0,0,0.35)", padding: 12, borderRadius: 12, border: "1px solid rgba(255,255,255,0.10)", height: 420, overflow: "auto" }}>
                  {mcpDiscoverResult ? JSON.stringify(mcpDiscoverResult, null, 2) : "Click 'Discover Tools'."}
                </pre>
              </Card>
            </div>
          )}
        </div>

        <div style={{ marginTop: 18, opacity: 0.65, fontSize: 12 }}>
          Dev API keys in <code>apps/api/config/users.json</code> → login gives JWT session. Budgets & caps in <code>apps/api/config/tenants/*.json</code>.
        </div>
      </div>
    </div>
  );
}
