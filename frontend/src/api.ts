import type { Session } from "./types";

export const API_BASE = (import.meta.env.VITE_API_URL || "http://127.0.0.1:8787").replace(
  /\/$/,
  "",
);

export const AGENT_BASE = (
  import.meta.env.VITE_AGENT_URL || "http://127.0.0.1:9877"
).replace(/\/$/, "");

async function parseError(res: Response): Promise<string> {
  try {
    const data = await res.json();
    return data.detail || data.error || res.statusText;
  } catch {
    return res.statusText;
  }
}

export async function createSession(repoUrl: string): Promise<Session> {
  const res = await fetch(`${API_BASE}/sessions`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ repoUrl }),
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

export async function probeLocalAgent(): Promise<boolean> {
  try {
    const res = await fetch(`${AGENT_BASE}/health`, { method: "GET" });
    if (!res.ok) return false;
    const data = await res.json();
    return Boolean(data?.ok);
  } catch {
    return false;
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
  } catch {
    return {
      ok: false,
      error:
        "Local agent is not running. Start the backend (it auto-starts the agent) or run: python agent/setup_check.py serve",
    };
  }
}

/** Re-fingerprint this machine and refresh the score (still no silent installs). */
export async function rescanSession(sessionId: string): Promise<{ ok: boolean; error?: string }> {
  return triggerAgentRun(sessionId);
}

export async function triggerLocalFolderCheck(path: string): Promise<{
  ok: boolean;
  session?: Session;
  sessionId?: string;
  error?: string;
}> {
  try {
    const res = await fetch(`${AGENT_BASE}/local`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        path,
        api: API_BASE,
        skip_install: true,
        skip_boot: true,
      }),
    });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) {
      return { ok: false, error: data.error || data.detail || `Agent returned ${res.status}` };
    }
    return {
      ok: true,
      session: data.session as Session | undefined,
      sessionId: data.session_id || data.session?.session_id,
    };
  } catch {
    return {
      ok: false,
      error:
        "Local agent is not running. Start: python backend/dev_server.py (auto-starts agent) or python agent/setup_check.py serve",
    };
  }
}

/** User-approved install for one blocker / software item. */
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
        approved: true,
      }),
    });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) {
      return { ok: false, error: data.error || `Agent returned ${res.status}` };
    }
    return { ok: true };
  } catch {
    return { ok: false, error: "Local agent is offline — cannot install." };
  }
}
