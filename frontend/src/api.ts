import type { Fingerprint, Session } from "./types";

export const API_BASE = (import.meta.env.VITE_API_URL || "http://127.0.0.1:8787").replace(
  /\/$/,
  "",
);

export const AGENT_BASE = (
  import.meta.env.VITE_AGENT_URL || "http://127.0.0.1:9877"
).replace(/\/$/, "");

/** Amplify HTTPS cannot call loopback — use cloud agent-code polling instead. */
export const USE_CLOUD_AGENT =
  typeof window !== "undefined" &&
  (window.location.protocol === "https:" || /amazonaws\.com/i.test(API_BASE));

const AGENT_CODE_KEY = "setup_readiness_agent_code";

export function getSavedAgentCode(): string {
  try {
    return (localStorage.getItem(AGENT_CODE_KEY) || "").trim().toLowerCase();
  } catch {
    return "";
  }
}

export function saveAgentCode(code: string) {
  try {
    localStorage.setItem(AGENT_CODE_KEY, code.trim().toLowerCase());
  } catch {
    /* ignore */
  }
}

/** Short hex code the laptop agent and this browser share. */
export function generateAgentCode(): string {
  const bytes = new Uint8Array(4);
  crypto.getRandomValues(bytes);
  return Array.from(bytes, (b) => b.toString(16).padStart(2, "0")).join("");
}

/** PowerShell one-liner — no .cmd download (avoids Smart App Control blocks). */
export function agentLinkCommand(code: string): string {
  const c = code.trim().toLowerCase();
  // Prefer `py -3` on Windows (avoids Store stub); fall back to python.
  return (
    `irm ${API_BASE}/agent.py -OutFile $env:TEMP\\setup_check.py; ` +
    `if (Get-Command py -ErrorAction SilentlyContinue) { ` +
    `py -3 $env:TEMP\\setup_check.py serve --api ${API_BASE} --code ${c} ` +
    `} else { ` +
    `python $env:TEMP\\setup_check.py serve --api ${API_BASE} --code ${c} }`
  );
}

export function connectAgentDownloadUrl(): string {
  const ret =
    typeof window !== "undefined" ? window.location.origin + window.location.pathname : "";
  return `${API_BASE}/agent/connect.cmd?return=${encodeURIComponent(ret || "https://main.d3qwc7ge49pla9.amplifyapp.com")}`;
}

async function parseError(res: Response): Promise<string> {
  try {
    const data = await res.json();
    return data.detail || data.error || res.statusText;
  } catch {
    return res.statusText;
  }
}

export async function createSession(repoUrl: string, agentCode?: string): Promise<Session> {
  const res = await fetch(`${API_BASE}/sessions`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      repoUrl,
      agent_code: (agentCode || getSavedAgentCode() || undefined) || undefined,
    }),
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok && !data.session_id) {
    throw new Error(data.detail || data.error || `Request failed (${res.status})`);
  }
  return data as Session;
}

export async function getSession(sessionId: string): Promise<Session> {
  const res = await fetch(`${API_BASE}/sessions/${sessionId}`);
  if (!res.ok) {
    throw new Error(await parseError(res));
  }
  return res.json();
}

export type AgentProbe = {
  online: boolean;
  fingerprint?: Fingerprint | null;
};

export async function probeCloudAgent(code?: string): Promise<AgentProbe> {
  const c = (code || getSavedAgentCode()).trim().toLowerCase();
  if (!c) return { online: false };
  try {
    const res = await fetch(`${API_BASE}/agent/status?code=${encodeURIComponent(c)}`, {
      cache: "no-store",
    });
    if (!res.ok) return { online: false };
    const data = await res.json();
    return {
      online: Boolean(data?.online),
      fingerprint: (data?.fingerprint as Fingerprint) || null,
    };
  } catch {
    return { online: false };
  }
}

export async function probeLocalAgent(): Promise<AgentProbe> {
  if (USE_CLOUD_AGENT) {
    return probeCloudAgent();
  }
  try {
    const res = await fetch(`${AGENT_BASE}/health`, {
      method: "GET",
      mode: "cors",
      cache: "no-store",
    });
    if (!res.ok) return { online: false };
    const data = await res.json();
    return { online: Boolean(data?.ok) };
  } catch {
    return { online: false };
  }
}

export async function probeBedrock(): Promise<boolean> {
  try {
    const res = await fetch(`${API_BASE}/health`, { method: "GET" });
    if (!res.ok) return false;
    const data = await res.json();
    return Boolean(data?.llm?.enabled || data?.bedrock?.enabled);
  } catch {
    return false;
  }
}

/** Fingerprint-only auto run — never installs until the user approves each item. */
export async function triggerAgentRun(sessionId: string): Promise<{ ok: boolean; error?: string }> {
  // On Amplify the laptop agent polls /agent/pending — no browser→localhost call.
  if (USE_CLOUD_AGENT) {
    if (!getSavedAgentCode()) {
      return { ok: false, error: "Enter the AGENT CODE from your laptop terminal first" };
    }
    return { ok: true };
  }
  try {
    const res = await fetch(`${AGENT_BASE}/run`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        session_id: sessionId,
        api: API_BASE,
        skip_install: true,
        skip_boot: true,
      }),
    });
    if (!res.ok) {
      const data = await res.json().catch(() => ({}));
      return { ok: false, error: data.error || `Agent returned ${res.status}` };
    }
    return { ok: true };
  } catch (err) {
    return { ok: false, error: err instanceof Error ? err.message : "Agent unreachable" };
  }
}

export async function createLocalIntent(
  localPath: string,
  agentCode?: string,
): Promise<Session> {
  const code = (agentCode || getSavedAgentCode()).trim();
  if (!code) {
    throw new Error("Link this laptop first — paste the agent code from the terminal");
  }
  const res = await fetch(`${API_BASE}/sessions/local-intent`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      local_path: localPath,
      agent_code: code,
    }),
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok && !data.session_id) {
    throw new Error(data.error || data.detail || `Request failed (${res.status})`);
  }
  return data as Session;
}

export async function stopLaptopAgent(code?: string): Promise<{ ok: boolean; error?: string }> {
  const c = (code || getSavedAgentCode()).trim().toLowerCase();
  if (!c) return { ok: false, error: "No laptop linked" };
  try {
    const res = await fetch(`${API_BASE}/agent/stop`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ code: c }),
    });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) return { ok: false, error: data.error || `Stop failed (${res.status})` };
    return { ok: true };
  } catch (err) {
    return { ok: false, error: err instanceof Error ? err.message : "Stop failed" };
  }
}

export async function triggerLocalFolderCheck(
  localPath: string,
): Promise<{ ok: boolean; session?: Session; sessionId?: string; error?: string }> {
  if (USE_CLOUD_AGENT) {
    try {
      const session = await createLocalIntent(localPath);
      return { ok: true, session, sessionId: session.session_id };
    } catch (err) {
      return { ok: false, error: err instanceof Error ? err.message : "Local intent failed" };
    }
  }
  try {
    const res = await fetch(`${AGENT_BASE}/local`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        path: localPath,
        api: API_BASE,
        agent_code: getSavedAgentCode() || undefined,
        skip_install: true,
        skip_boot: true,
      }),
    });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) {
      return { ok: false, error: data.error || `Agent returned ${res.status}` };
    }
    const session = (data.session || data) as Session;
    return {
      ok: true,
      session,
      sessionId: session.session_id || data.session_id,
    };
  } catch (err) {
    return { ok: false, error: err instanceof Error ? err.message : "Agent unreachable" };
  }
}

export async function approveInstall(
  sessionId: string,
  blockerId: string,
): Promise<{ ok: boolean; error?: string }> {
  try {
    const res = await fetch(`${AGENT_BASE}/approve`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        session_id: sessionId,
        api: API_BASE,
        action: blockerId,
        blocker_id: blockerId,
        approved: true,
      }),
    });
    if (!res.ok) {
      const data = await res.json().catch(() => ({}));
      return { ok: false, error: data.error || `Agent returned ${res.status}` };
    }
    return { ok: true };
  } catch (err) {
    return { ok: false, error: err instanceof Error ? err.message : "Agent unreachable" };
  }
}

export async function rescanSession(sessionId: string): Promise<{ ok: boolean; error?: string }> {
  return triggerAgentRun(sessionId);
}
