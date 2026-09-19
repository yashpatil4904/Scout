import { FormEvent, useEffect, useMemo, useState, type ReactNode } from "react";
import AgentStatus from "./components/AgentStatus";
import BlockerList from "./components/BlockerList";
import CompatTable from "./components/CompatTable";
import CrashTimeline from "./components/CrashTimeline";
import ScoreGauge from "./components/ScoreGauge";
import {
  API_BASE,
  approveInstall,
  createSession,
  getSession,
  probeBedrock,
  probeLocalAgent,
  rescanSession,
  triggerAgentRun,
  triggerLocalFolderCheck,
} from "./api";
import type { Session } from "./types";

type Mode = "github" | "local";

const EXAMPLES = [
  "https://github.com/pallets/flask",
  "https://github.com/expressjs/express",
  "https://github.com/octocat/Hello-World",
];

export default function App() {
  const [mode, setMode] = useState<Mode>("github");
  const [url, setUrl] = useState("");
  const [localPath, setLocalPath] = useState("");
  const [busy, setBusy] = useState(false);
  const [agentPhase, setAgentPhase] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [session, setSession] = useState<Session | null>(null);
  const [agentOnline, setAgentOnline] = useState<boolean | null>(null);
  const [bedrockOn, setBedrockOn] = useState<boolean | null>(null);

  useEffect(() => {
    let cancelled = false;
    async function tick() {
      const [ok, ai] = await Promise.all([probeLocalAgent(), probeBedrock()]);
      if (!cancelled) {
        setAgentOnline(ok);
        setBedrockOn(ai);
      }
    }
    tick();
    const id = window.setInterval(tick, 4000);
    return () => {
      cancelled = true;
      window.clearInterval(id);
    };
  }, []);

  useEffect(() => {
    if (!session?.session_id) return;
    if (session.status === "complete" || session.status === "error") return;
    const id = session.session_id;
    const timer = window.setInterval(async () => {
      try {
        const next = await getSession(id);
        setSession(next);
        if (next.status === "complete" || next.status === "error") {
          setAgentPhase(null);
          setBusy(false);
        }
      } catch {
        /* keep polling */
      }
    }, 2000);
    return () => window.clearInterval(timer);
  }, [session?.session_id, session?.status]);

  async function onGithubSubmit(event: FormEvent) {
    event.preventDefault();
    setError(null);
    setBusy(true);
    setAgentPhase("Analyzing repository…");
    setSession(null);
    try {
      const created = await createSession(url.trim());
      setSession(created);
      if (created.status === "error") {
        setError(created.error || "Could not analyze that repository");
        setBusy(false);
        setAgentPhase(null);
        return;
      }
      setAgentPhase("Fingerprinting this machine (no installs yet)…");
      const triggered = await triggerAgentRun(created.session_id);
      if (!triggered.ok) {
        // Backend may have already auto-triggered; probe again briefly.
        const online = await probeLocalAgent();
        setAgentOnline(online);
        if (!online) {
          setError(triggered.error || "Local agent offline");
          setAgentPhase(null);
          setBusy(false);
          return;
        }
        const retry = await triggerAgentRun(created.session_id);
        if (!retry.ok) {
          setError(retry.error || "Could not start local agent");
          setAgentPhase(null);
          setBusy(false);
        }
      }
    } catch (err) {
      setSession(null);
      setError(err instanceof Error ? err.message : "Request failed");
      setBusy(false);
      setAgentPhase(null);
    }
  }

  async function onLocalSubmit(event: FormEvent) {
    event.preventDefault();
    setError(null);
    setBusy(true);
    setAgentPhase("Reading local folder on this machine…");
    setSession(null);
    try {
      const online = await probeLocalAgent();
      setAgentOnline(online);
      if (!online) {
        setError(
          "Local agent is offline. Start the backend (`python backend/dev_server.py`) — it auto-starts the agent — or run `python agent/setup_check.py serve`.",
        );
        setBusy(false);
        setAgentPhase(null);
        return;
      }
      const result = await triggerLocalFolderCheck(localPath.trim());
      if (!result.ok || !result.sessionId) {
        setError(result.error || "Local check failed");
        setBusy(false);
        setAgentPhase(null);
        return;
      }
      if (result.session) {
        setSession(result.session);
      } else {
        const created = await getSession(result.sessionId);
        setSession(created);
      }
      setAgentPhase("Fingerprinting this machine (no installs yet)…");
    } catch (err) {
      setSession(null);
      setError(err instanceof Error ? err.message : "Request failed");
      setBusy(false);
      setAgentPhase(null);
    }
  }

  async function onApproveBlocker(blockerId: string) {
    if (!session?.session_id) return;
    const sid = session.session_id;
    const before = JSON.stringify(session.score?.blockers?.map((b) => b.id) || []);
    setAgentPhase(`Installing ${blockerId} (you approved)…`);
    const result = await approveInstall(sid, blockerId);
    if (!result.ok) {
      setError(result.error || "Install failed or was blocked");
      setAgentPhase(null);
      throw new Error(result.error || "Install failed");
    }
    for (let i = 0; i < 45; i++) {
      await new Promise((r) => window.setTimeout(r, 1500));
      try {
        const next = await getSession(sid);
        setSession(next);
        const after = JSON.stringify(next.score?.blockers?.map((b) => b.id) || []);
        if (next.status === "complete" && (after !== before || i > 2)) {
          // Give the agent a moment; then accept refreshed complete state
          if (i >= 2) {
            setAgentPhase(null);
            return;
          }
        }
      } catch {
        /* keep waiting */
      }
    }
    setAgentPhase(null);
  }

  async function onRescan() {
    if (!session?.session_id) return;
    setError(null);
    setBusy(true);
    setAgentPhase("Re-scanning this machine…");
    const result = await rescanSession(session.session_id);
    if (!result.ok) {
      setError(result.error || "Rescan failed");
      setBusy(false);
      setAgentPhase(null);
      return;
    }
    const sid = session.session_id;
    for (let i = 0; i < 20; i++) {
      await new Promise((r) => window.setTimeout(r, 1500));
      try {
        const next = await getSession(sid);
        setSession(next);
        if (next.status === "complete" && next.fingerprint) {
          setBusy(false);
          setAgentPhase(null);
          return;
        }
      } catch {
        /* keep waiting */
      }
    }
    setBusy(false);
    setAgentPhase(null);
  }

  const percent = session?.score?.percent ?? null;
  const label = useMemo(
    () => headline(session, busy, agentPhase, mode),
    [session, busy, agentPhase, mode],
  );

  return (
    <div className="min-h-screen bg-ink-950">
      <header className="flex items-baseline justify-between border-b border-ink-700 px-6 py-4 md:px-10">
        <div>
          <p className="font-mono text-[11px] uppercase tracking-[0.28em] text-slate-500">
            AWS Ship It
          </p>
          <h1 className="font-display text-2xl text-paper">Setup Readiness Checker</h1>
        </div>
        <div className="flex items-center gap-4">
          <p className="font-mono text-[11px] uppercase tracking-wider text-slate-500">
            {bedrockOn ? "LLM scoring on" : "heuristic scoring"}
          </p>
          <AgentStatus online={agentOnline} />
        </div>
      </header>

      <main className="mx-auto grid max-w-6xl gap-10 px-6 py-10 md:px-10 lg:grid-cols-[1.1fr_0.9fr]">
        <section>
          <div className="mb-4 flex gap-2">
            <ModeTab active={mode === "github"} onClick={() => setMode("github")}>
              GitHub URL
            </ModeTab>
            <ModeTab active={mode === "local"} onClick={() => setMode("local")}>
              Local folder
            </ModeTab>
          </div>

          {mode === "github" ? (
            <form onSubmit={onGithubSubmit} className="border border-ink-700 bg-ink-900 p-5">
              <label className="font-mono text-[11px] uppercase tracking-[0.2em] text-slate-500">
                Public GitHub repo
              </label>
              <p className="mt-2 text-sm text-slate-400">
                We fingerprint your PC first, infer what the repo needs, then tell you which
                software to install. Env/config is separate — nothing installs until you approve.
              </p>
              <div className="mt-3 flex flex-col gap-3 sm:flex-row">
                <input
                  value={url}
                  onChange={(e) => setUrl(e.target.value)}
                  placeholder="https://github.com/owner/repo"
                  className="w-full border border-ink-700 bg-ink-950 px-3 py-3 font-mono text-sm text-paper outline-none focus:border-moss"
                />
                <button
                  type="submit"
                  disabled={busy || !url.trim()}
                  className="shrink-0 bg-moss px-5 py-3 font-mono text-xs uppercase tracking-[0.16em] text-ink-950 disabled:opacity-40"
                >
                  {busy ? "Checking…" : "Check setup"}
                </button>
              </div>
              <div className="mt-3 flex flex-wrap gap-2">
                {EXAMPLES.map((example) => (
                  <button
                    key={example}
                    type="button"
                    onClick={() => setUrl(example)}
                    className="font-mono text-[11px] text-slate-500 hover:text-moss"
                  >
                    {example.replace("https://github.com/", "")}
                  </button>
                ))}
              </div>
              {error ? <p className="mt-3 font-mono text-sm text-rust">{error}</p> : null}
            </form>
          ) : (
            <form onSubmit={onLocalSubmit} className="border border-ink-700 bg-ink-900 p-5">
              <label className="font-mono text-[11px] uppercase tracking-[0.2em] text-slate-500">
                Folder you are working in
              </label>
              <p className="mt-2 text-sm text-slate-400">
                Checks the project on this laptop. First pass is fingerprint-only; each missing
                tool or dependency install asks for your OK.
              </p>
              <div className="mt-3 flex flex-col gap-3 sm:flex-row">
                <input
                  value={localPath}
                  onChange={(e) => setLocalPath(e.target.value)}
                  placeholder={
                    /Win/i.test(navigator.userAgent)
                      ? "C:\\Users\\you\\Desktop\\my-project"
                      : "/Users/you/projects/my-project"
                  }
                  className="w-full border border-ink-700 bg-ink-950 px-3 py-3 font-mono text-sm text-paper outline-none focus:border-moss"
                />
                <button
                  type="submit"
                  disabled={busy || !localPath.trim()}
                  className="shrink-0 bg-moss px-5 py-3 font-mono text-xs uppercase tracking-[0.16em] text-ink-950 disabled:opacity-40"
                >
                  {busy ? "Checking…" : "Check folder"}
                </button>
              </div>
              <p className="mt-3 font-mono text-[11px] text-slate-500">
                CLI equivalent:{" "}
                <span className="text-moss">
                  python agent/setup_check.py --path &quot;{localPath || "."}&quot; --api {API_BASE}
                </span>
              </p>
              {error ? <p className="mt-3 font-mono text-sm text-rust">{error}</p> : null}
            </form>
          )}

          <div className="mt-8 flex flex-col items-center gap-4 lg:items-start">
            <ScoreGauge percent={percent} label={label} />
            {session?.session_id && session.status === "complete" ? (
              <button
                type="button"
                onClick={onRescan}
                disabled={busy}
                className="border border-ink-700 px-4 py-2 font-mono text-[11px] uppercase tracking-wider text-slate-300 hover:border-moss hover:text-moss disabled:opacity-40"
              >
                {busy ? "Scanning…" : "Rescan this machine"}
              </button>
            ) : null}
          </div>

          {session?.fingerprint ? (
            <dl className="mt-8 grid grid-cols-2 gap-3 font-mono text-xs text-slate-400 sm:grid-cols-3 lg:grid-cols-4">
              <Fact
                label="This OS"
                value={`${session.fingerprint.os || "?"} ${session.fingerprint.arch || ""}`}
              />
              <Fact
                label="RAM"
                value={
                  session.fingerprint.ram_mb
                    ? `${Math.round(session.fingerprint.ram_mb / 1024)} GB`
                    : "—"
                }
              />
              <Fact label="Python here" value={session.fingerprint.python || "missing"} />
              <Fact label="Node here" value={session.fingerprint.node || "missing"} />
              <Fact label="npm here" value={session.fingerprint.npm || "missing"} />
              <Fact label="git here" value={session.fingerprint.git || "missing"} />
              <Fact label="Docker here" value={session.fingerprint.docker || "missing"} />
              <Fact
                label="Tools on PATH"
                value={(session.fingerprint.tools || []).slice(0, 6).join(", ") || "—"}
              />
            </dl>
          ) : null}
        </section>

        <aside className="flex flex-col gap-5">
          <div className="border border-ink-700 bg-ink-900 p-5 text-sm text-slate-400">
            {agentOnline ? (
              <>
                <p className="font-mono text-[11px] uppercase tracking-[0.2em] text-moss">
                  Local agent connected
                </p>
                <p className="mt-2">
                  Checks run on <em className="text-paper">this</em> laptop. Missing software is{" "}
                  <em className="text-paper">not</em> installed until you click Install on each
                  blocker.
                </p>
              </>
            ) : (
              <>
                <p className="font-mono text-[11px] uppercase tracking-[0.2em] text-pollen">
                  Start local agent once
                </p>
                <p className="mt-2">
                  The browser cannot fingerprint your machine by itself. Start the API (it launches
                  the agent) or run:
                </p>
                <pre className="mt-3 overflow-x-auto bg-ink-950 p-3 font-mono text-xs text-moss">
                  python backend/dev_server.py
                </pre>
              </>
            )}
            {agentPhase ? (
              <p className="mt-3 font-mono text-xs text-pollen">{agentPhase}</p>
            ) : null}
          </div>

          {session?.crash_preview?.frames?.length ? (
            <CrashTimeline preview={session.crash_preview} />
          ) : null}

          {session?.requirements && session?.fingerprint ? (
            <CompatTable requirements={session.requirements} fingerprint={session.fingerprint} />
          ) : null}

          {session?.requirements ? (
            <div className="border border-ink-700 bg-ink-900 p-5">
              <p className="font-mono text-[11px] uppercase tracking-[0.2em] text-slate-500">
                What {session.source === "local" ? "this folder" : "the repo"} needs
                {session.requirements.inferred ? " · inferred" : ""}
                {session.requirements.notes?.some((n) => n.includes("RepoAnalystAgent"))
                  ? " · LLM"
                  : ""}
              </p>
              {session.local_path ? (
                <p className="mt-2 truncate font-mono text-xs text-moss">{session.local_path}</p>
              ) : null}
              <ul className="mt-3 space-y-1 font-mono text-xs text-slate-300">
                <li>runtime {describeRuntime(session)}</li>
                <li>install {session.requirements.install_command || "none"}</li>
                <li>start {session.requirements.start_command || "none (not a bootable app)"}</li>
                {session.requirements.packages?.length ? (
                  <li>packages {session.requirements.packages.slice(0, 8).join(", ")}</li>
                ) : null}
                {session.requirements.env_vars?.length ? (
                  <li>env {session.requirements.env_vars.join(", ")}</li>
                ) : null}
                {session.requirements.services?.length ? (
                  <li>services {session.requirements.services.join(", ")}</li>
                ) : null}
                {session.requirements.manifests_found?.length ? (
                  <li>files {session.requirements.manifests_found.join(", ")}</li>
                ) : (
                  <li>no manifests — inferred from source</li>
                )}
              </ul>
            </div>
          ) : null}

          {session?.score ? (
            <div>
              <BlockerList
                key={`${session.session_id}-${session.score.percent}-${(session.score.blockers || [])
                  .map((b) => b.id)
                  .join(",")}`}
                blockers={session.score.blockers || []}
                sessionId={session.session_id}
                onApprove={onApproveBlocker}
              />
            </div>
          ) : null}

          {session?.install?.diagnosis ? (
            <div className="border border-rust bg-ink-900 p-5">
              <p className="font-mono text-[11px] uppercase tracking-[0.2em] text-rust">Root cause</p>
              <p className="mt-2 text-sm leading-relaxed text-paper">{session.install.diagnosis}</p>
            </div>
          ) : null}
        </aside>
      </main>
    </div>
  );
}

function ModeTab({
  active,
  onClick,
  children,
}: {
  active: boolean;
  onClick: () => void;
  children: ReactNode;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      className={
        active
          ? "border border-moss bg-ink-900 px-4 py-2 font-mono text-xs uppercase tracking-wider text-moss"
          : "border border-ink-700 bg-ink-950 px-4 py-2 font-mono text-xs uppercase tracking-wider text-slate-500 hover:text-paper"
      }
    >
      {children}
    </button>
  );
}

function Fact({ label, value }: { label: string; value: string }) {
  return (
    <div className="border border-ink-700 bg-ink-900 px-3 py-2">
      <dt className="text-[10px] uppercase tracking-wider text-slate-500">{label}</dt>
      <dd className="mt-1 truncate text-paper">{value}</dd>
    </div>
  );
}

function headline(
  session: Session | null,
  busy: boolean,
  agentPhase: string | null,
  mode: Mode,
): string {
  if (agentPhase) return agentPhase;
  if (busy) return mode === "local" ? "Checking local folder…" : "Reading the repository…";
  if (!session) {
    return mode === "local"
      ? "Point at a folder on this laptop. Get one number back."
      : "Paste a GitHub URL. Get one number back.";
  }
  if (session.status === "error") return session.error || "Analyze failed";
  if (session.status === "awaiting_agent") return "Local agent is running the check…";
  if (session.status === "scoring") return "Comparing this laptop to the project…";
  if (session.score?.summary) return session.score.summary;
  return "Waiting for the local agent";
}

function describeRuntime(session: Session): string {
  const req = session.requirements;
  if (!req) return "unknown";
  const want = [req.runtime, req.runtime_constraint || req.runtime_version]
    .filter(Boolean)
    .join(" ");
  return want || "unknown";
}
