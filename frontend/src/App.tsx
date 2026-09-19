import { FormEvent, useEffect, useMemo, useState, type ReactNode } from "react";
import AgentStatus from "./components/AgentStatus";
import BlockerList from "./components/BlockerList";
import CompatTable from "./components/CompatTable";
import CrashTimeline from "./components/CrashTimeline";
import Landing from "./components/Landing";
import ScoreGauge from "./components/ScoreGauge";
import {
  API_BASE,
  USE_CLOUD_AGENT,
  agentLinkCommand,
  approveInstall,
  connectAgentDownloadUrl,
  createSession,
  generateAgentCode,
  getSavedAgentCode,
  getSession,
  probeBedrock,
  probeLocalAgent,
  rescanSession,
  saveAgentCode,
  stopLaptopAgent,
  triggerAgentRun,
  triggerLocalFolderCheck,
} from "./api";
import type { Fingerprint, Session } from "./types";

type Mode = "github" | "local";
type View = "landing" | "app";

const EXAMPLES = [
  "https://github.com/pallets/flask",
  "https://github.com/expressjs/express",
  "https://github.com/tiangolo/fastapi",
];

export default function App() {
  const [view, setView] = useState<View>(() => {
    if (typeof window === "undefined") return "landing";
    const params = new URLSearchParams(window.location.search);
    if (params.get("code") || params.get("app") === "1") return "app";
    try {
      return sessionStorage.getItem("repoready_view") === "app" ? "app" : "landing";
    } catch {
      return "landing";
    }
  });
  const [mode, setMode] = useState<Mode>("github");
  const [url, setUrl] = useState("");
  const [localPath, setLocalPath] = useState("");
  const [busy, setBusy] = useState(false);
  const [agentPhase, setAgentPhase] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [session, setSession] = useState<Session | null>(null);
  const [agentOnline, setAgentOnline] = useState<boolean | null>(null);
  const [liveFingerprint, setLiveFingerprint] = useState<Fingerprint | null>(null);
  const [bedrockOn, setBedrockOn] = useState<boolean | null>(null);
  const [agentCodeInput, setAgentCodeInput] = useState(() => {
    const saved = getSavedAgentCode();
    if (saved.length >= 4) return saved;
    const next = generateAgentCode();
    saveAgentCode(next);
    return next;
  });
  const [stoppingAgent, setStoppingAgent] = useState(false);

  function openApp() {
    setView("app");
    try {
      sessionStorage.setItem("repoready_view", "app");
    } catch {
      /* ignore */
    }
  }

  function openLanding() {
    setView("landing");
    try {
      sessionStorage.setItem("repoready_view", "landing");
    } catch {
      /* ignore */
    }
  }

  // Auto-link when connect.cmd opens Amplify with ?code=
  useEffect(() => {
    const params = new URLSearchParams(window.location.search);
    const code = (params.get("code") || "").trim().toLowerCase();
    if (code.length >= 4) {
      saveAgentCode(code);
      setAgentCodeInput(code);
      openApp();
      params.delete("code");
      const next = `${window.location.pathname}${params.toString() ? `?${params}` : ""}${window.location.hash}`;
      window.history.replaceState({}, "", next);
    }
  }, []);

  useEffect(() => {
    let cancelled = false;
    async function tick() {
      const [probe, ai] = await Promise.all([probeLocalAgent(), probeBedrock()]);
      if (!cancelled) {
        setAgentOnline(probe.online);
        setLiveFingerprint(probe.fingerprint || null);
        setBedrockOn(ai);
      }
    }
    tick();
    const id = window.setInterval(tick, USE_CLOUD_AGENT ? 3000 : 4000);
    return () => {
      cancelled = true;
      window.clearInterval(id);
    };
  }, [agentCodeInput]);

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
      if (USE_CLOUD_AGENT) {
        const probe = await probeLocalAgent();
        setAgentOnline(probe.online);
        setLiveFingerprint(probe.fingerprint || null);
        if (!probe.online) {
          setError("Laptop agent not linked. Copy the PowerShell command, run it, then retry.");
          setBusy(false);
          setAgentPhase(null);
          return;
        }
        if (!probe.fingerprint?.python && !probe.fingerprint?.node && !probe.fingerprint?.git) {
          setError(
            "Agent is online but has not reported PC tools yet. Wait 3 seconds and retry — or restart the agent.",
          );
          setBusy(false);
          setAgentPhase(null);
          return;
        }
      }
      const created = await createSession(url.trim());
      setSession(created);
      if (created.status === "error") {
        setError(created.error || "Could not analyze that repository");
        setBusy(false);
        setAgentPhase(null);
        return;
      }
      setAgentPhase(
        USE_CLOUD_AGENT
          ? "Waiting for your laptop agent (cloud poll)…"
          : "Fingerprinting this machine (no installs yet)…",
      );
      const triggered = await triggerAgentRun(created.session_id);
      if (!triggered.ok) {
        setError(triggered.error || "Connect laptop agent first");
        setAgentPhase(null);
        setBusy(false);
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
      setAgentOnline(online.online);
      setLiveFingerprint(online.fingerprint || null);
      if (!online.online) {
        setError(
          USE_CLOUD_AGENT
            ? "Laptop agent not linked. Copy the PowerShell command, run it, and leave that window open."
            : "Local agent is offline. Run python agent/setup_check.py serve",
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
      setAgentPhase(
        USE_CLOUD_AGENT
          ? "Laptop agent is reading that folder…"
          : "Fingerprinting this machine (no installs yet)…",
      );
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

  async function onStopAgent() {
    setStoppingAgent(true);
    setError(null);
    const result = await stopLaptopAgent();
    setStoppingAgent(false);
    if (!result.ok) {
      setError(result.error || "Could not stop agent");
      return;
    }
    setAgentOnline(false);
    setAgentPhase(null);
  }

  const percent = session?.score?.percent ?? null;
  const label = useMemo(
    () => headline(session, busy, agentPhase, mode),
    [session, busy, agentPhase, mode],
  );

  if (view === "landing") {
    return <Landing onStart={openApp} />;
  }

  return (
    <div className="min-h-screen bg-[#f4f5f7]">
      <header className="hairline sticky top-0 z-20">
        <div className="mx-auto flex max-w-6xl items-center justify-between gap-4 px-6 py-4 md:px-10">
          <div className="flex items-center gap-4">
            <button
              type="button"
              onClick={openLanding}
              className="font-display text-lg font-bold tracking-tight text-ink-900 hover:text-sea"
            >
              Scout
            </button>
            <span className="hidden font-mono text-[10px] uppercase tracking-wider text-ink-600 sm:inline">
              {bedrockOn ? "AI scoring" : "heuristic"}
            </span>
          </div>
          <AgentStatus online={agentOnline} onStop={onStopAgent} stopping={stoppingAgent} />
        </div>
      </header>

      <main className="mx-auto grid max-w-6xl gap-8 px-6 py-8 md:px-10 lg:grid-cols-[1fr_0.95fr] lg:gap-8">
        <section className="space-y-6">
          <div className="panel p-6 md:p-8">
            <p className="section-label">Check this laptop</p>
            <h1 className="mt-2 font-display text-3xl font-bold tracking-tight text-ink-900 md:text-4xl">
              What should I install?
            </h1>

            <div className="mt-8 flex gap-6 border-b border-[#d0d7e0]">
              <ModeTab active={mode === "github"} onClick={() => setMode("github")}>
                GitHub URL
              </ModeTab>
              <ModeTab active={mode === "local"} onClick={() => setMode("local")}>
                Local folder
              </ModeTab>
            </div>

            {mode === "github" ? (
              <form onSubmit={onGithubSubmit} className="mt-6 space-y-4">
                <label className="section-label">Public GitHub repo</label>
                <p className="text-sm leading-relaxed text-ink-700">
                  We infer runtime and deps, then compare them to{" "}
                  <em className="font-medium text-ink-900">this</em> PC. Nothing installs until you
                  approve.
                </p>
                <div className="flex flex-col gap-3 sm:flex-row sm:items-stretch">
                  <input
                    value={url}
                    onChange={(e) => setUrl(e.target.value)}
                    placeholder="https://github.com/owner/repo"
                    className="field flex-1"
                  />
                  <button
                    type="submit"
                    disabled={busy || !url.trim()}
                    className="btn-primary shrink-0"
                  >
                    {busy ? "Checking…" : "Check my laptop"}
                  </button>
                </div>
                <div className="flex flex-wrap gap-x-4 gap-y-1">
                  {EXAMPLES.map((example) => (
                    <button
                      key={example}
                      type="button"
                      onClick={() => setUrl(example)}
                      className="font-mono text-[11px] text-ink-600 hover:text-sea"
                    >
                      {example.replace("https://github.com/", "")}
                    </button>
                  ))}
                </div>
                {error ? <p className="text-sm font-medium text-rust">{error}</p> : null}
              </form>
            ) : (
              <form onSubmit={onLocalSubmit} className="mt-6 space-y-4">
                <label className="section-label">Folder on this PC</label>
                <p className="text-sm leading-relaxed text-ink-700">
                  Your linked agent reads that path locally — only manifests/snippets go to the
                  cloud.
                </p>
                <div className="flex flex-col gap-3 sm:flex-row sm:items-stretch">
                  <input
                    value={localPath}
                    onChange={(e) => setLocalPath(e.target.value)}
                    placeholder={
                      /Win/i.test(navigator.userAgent)
                        ? "C:\\Users\\you\\Desktop\\my-project"
                        : "/Users/you/projects/my-project"
                    }
                    className="field flex-1"
                  />
                  <button
                    type="submit"
                    disabled={busy || !localPath.trim()}
                    className="btn-primary shrink-0"
                  >
                    {busy ? "Checking…" : "Check folder"}
                  </button>
                </div>
                <details>
                  <summary className="cursor-pointer font-mono text-[11px] text-ink-600">
                    Prefer CLI?
                  </summary>
                  <p className="code-block mt-2 break-all text-[11px]">
                    python agent/setup_check.py --path &quot;{localPath || "."}&quot; --api{" "}
                    {API_BASE}
                  </p>
                </details>
                {error ? <p className="text-sm font-medium text-rust">{error}</p> : null}
              </form>
            )}
          </div>

          <div className="panel flex flex-col items-center gap-4 p-6 md:items-start md:p-8">
            <ScoreGauge percent={percent} label={label} />
            {session?.session_id && session.status === "complete" ? (
              <button
                type="button"
                onClick={onRescan}
                disabled={busy}
                className="btn-ghost text-xs"
              >
                {busy ? "Scanning…" : "Rescan this machine"}
              </button>
            ) : null}
          </div>

          {session?.fingerprint ? (
            <div className="panel p-6 md:p-8">
              <p className="section-label mb-4">Fingerprint</p>
              <dl className="grid grid-cols-2 gap-x-6 gap-y-4 font-mono text-xs sm:grid-cols-3">
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
                <Fact label="Python" value={session.fingerprint.python || "missing"} />
                <Fact label="Node" value={session.fingerprint.node || "missing"} />
                <Fact label="npm" value={session.fingerprint.npm || "missing"} />
                <Fact label="git" value={session.fingerprint.git || "missing"} />
                <Fact label="Docker" value={session.fingerprint.docker || "missing"} />
                <Fact
                  label="Tools"
                  value={(session.fingerprint.tools || []).slice(0, 6).join(", ") || "—"}
                />
              </dl>
            </div>
          ) : null}
        </section>

        <aside className="space-y-6">
          <div className="panel p-6 text-sm text-ink-800 md:p-8">
            {agentOnline ? (
              <>
                <p className="section-label text-sea">Laptop linked</p>
                <p className="mt-2 leading-relaxed text-ink-700">
                  Checks run on your machine. Use <em className="font-medium text-ink-900">Stop agent</em>{" "}
                  in the header when you&apos;re done.
                </p>
                {liveFingerprint ? (
                  <dl className="mt-5 grid grid-cols-2 gap-4 border-t border-[#d0d7e0] pt-5 font-mono text-[12px]">
                    <div>
                      <dt className="text-ink-600">Python</dt>
                      <dd className="mt-0.5 truncate font-medium text-ink-900">
                        {liveFingerprint.python || "—"}
                      </dd>
                    </div>
                    <div>
                      <dt className="text-ink-600">Node</dt>
                      <dd className="mt-0.5 truncate font-medium text-ink-900">
                        {liveFingerprint.node || "—"}
                      </dd>
                    </div>
                    <div>
                      <dt className="text-ink-600">git</dt>
                      <dd className="mt-0.5 truncate font-medium text-ink-900">
                        {liveFingerprint.git || "—"}
                      </dd>
                    </div>
                    <div>
                      <dt className="text-ink-600">OS</dt>
                      <dd className="mt-0.5 truncate font-medium text-ink-900">
                        {liveFingerprint.os || "—"} {liveFingerprint.arch || ""}
                      </dd>
                    </div>
                  </dl>
                ) : (
                  <p className="mt-3 font-mono text-[11px] font-medium text-pollen">
                    Waiting for PC tool report…
                  </p>
                )}
              </>
            ) : (
              <AgentBootstrap
                agentCodeInput={agentCodeInput}
                setAgentCodeInput={setAgentCodeInput}
                onCodeSaved={() => {}}
              />
            )}
            {agentPhase ? (
              <p className="mt-4 font-mono text-xs font-medium text-pollen">{agentPhase}</p>
            ) : null}
          </div>

          {session?.crash_preview?.frames?.length ? (
            <div className="panel p-6 md:p-8">
              <CrashTimeline preview={session.crash_preview} />
            </div>
          ) : null}

          {session?.requirements && session?.fingerprint ? (
            <div className="panel p-6 md:p-8">
              <CompatTable requirements={session.requirements} fingerprint={session.fingerprint} />
            </div>
          ) : null}

          {session?.requirements ? (
            <div className="panel p-6 md:p-8">
              <p className="section-label">
                Project needs
                {session.requirements.inferred ? " · inferred" : ""}
                {session.requirements.notes?.some((n) => n.includes("RepoAnalystAgent"))
                  ? " · AI"
                  : ""}
              </p>
              {session.local_path ? (
                <p className="mt-2 truncate font-mono text-xs font-medium text-sea">
                  {session.local_path}
                </p>
              ) : null}
              <ul className="mt-4 space-y-2 font-mono text-xs text-ink-800">
                <li>runtime {describeRuntime(session)}</li>
                <li>install {session.requirements.install_command || "none"}</li>
                <li>start {session.requirements.start_command || "none"}</li>
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
            <div className="panel p-6 md:p-8">
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
            <div className="panel border-l-4 border-l-rust p-6 md:p-8">
              <p className="section-label text-rust">Root cause</p>
              <p className="mt-2 text-sm leading-relaxed text-ink-900">
                {session.install.diagnosis}
              </p>
            </div>
          ) : null}
        </aside>
      </main>
    </div>
  );
}

function AgentBootstrap({
  agentCodeInput,
  setAgentCodeInput,
  onCodeSaved,
}: {
  agentCodeInput: string;
  setAgentCodeInput: (v: string) => void;
  onCodeSaved: () => void;
}) {
  const [copied, setCopied] = useState(false);
  const downloadUrl = connectAgentDownloadUrl();
  const code = agentCodeInput.length >= 4 ? agentCodeInput : "";
  const command = code ? agentLinkCommand(code) : "";

  function ensureCode(): string {
    if (agentCodeInput.length >= 4) return agentCodeInput;
    const next = generateAgentCode();
    setAgentCodeInput(next);
    saveAgentCode(next);
    return next;
  }

  function saveCode() {
    const c = ensureCode();
    saveAgentCode(c);
    onCodeSaved();
  }

  async function copyCommand() {
    const c = ensureCode();
    saveAgentCode(c);
    const cmd = agentLinkCommand(c);
    await navigator.clipboard.writeText(cmd);
    setCopied(true);
    window.setTimeout(() => setCopied(false), 1600);
    onCodeSaved();
  }

  return (
    <>
      <p className="section-label text-pollen">Step 1 — link this laptop</p>
      <ol className="mt-3 list-decimal space-y-2 pl-5 text-sm leading-relaxed text-ink-600">
        <li>
          Copy the PowerShell command (code{" "}
          <span className="font-mono text-sea">{code || "……"}</span>)
        </li>
        <li>Paste in PowerShell — leave that window open</li>
        <li>
          Status becomes <em className="text-ink-900">laptop linked</em>
        </li>
      </ol>

      <div className="mt-5 flex flex-wrap items-end gap-3">
        <div>
          <label className="section-label">Agent code</label>
          <input
            value={agentCodeInput}
            onChange={(e) => setAgentCodeInput(e.target.value.trim().toLowerCase())}
            placeholder="generate"
            className="field mt-1 w-36"
          />
        </div>
        <button
          type="button"
          onClick={() => {
            const next = generateAgentCode();
            setAgentCodeInput(next);
            saveAgentCode(next);
            onCodeSaved();
          }}
          className="btn-ghost py-2 text-xs"
        >
          Generate
        </button>
        <button
          type="button"
          onClick={saveCode}
          disabled={agentCodeInput.length < 4}
          className="btn-ghost py-2 text-xs"
        >
          Use this code
        </button>
      </div>

      <pre className="code-block mt-4">{command || "Generate or enter an agent code first"}</pre>
      <button type="button" onClick={copyCommand} className="btn-primary mt-3">
        {copied ? "Copied — paste in PowerShell" : "Copy PowerShell command"}
      </button>
      <p className="mt-3 font-mono text-[10px] text-ink-600">
        Avoid .cmd downloads if Smart App Control blocks them. Stop anytime from the header.
      </p>

      <details className="mt-4">
        <summary className="cursor-pointer font-mono text-[11px] text-ink-600">
          Alternate: download connector .cmd
        </summary>
        <a href={downloadUrl} className="mt-3 inline-block text-sm text-sea hover:underline">
          Download Scout-Connect.cmd
        </a>
      </details>
    </>
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
          ? "-mb-px border-b-2 border-sea pb-3 font-display text-sm font-semibold text-sea"
          : "pb-3 font-display text-sm font-semibold text-ink-600 hover:text-ink-900"
      }
    >
      {children}
    </button>
  );
}

function Fact({ label, value }: { label: string; value: string }) {
  return (
    <div>
      <dt className="text-[10px] font-medium uppercase tracking-wider text-ink-600">{label}</dt>
      <dd className="mt-1 truncate font-medium text-ink-900">{value}</dd>
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
      ? "Link your laptop, then point at a project folder."
      : "Link your laptop, then paste a GitHub URL.";
  }
  if (session.status === "error") return session.error || "Check failed";
  if (session.status === "awaiting_agent") return "Your laptop agent is scanning…";
  if (session.status === "scoring") return "Comparing PC ↔ project…";
  if (session.score?.summary) return session.score.summary;
  return "Waiting for your laptop…";
}

function describeRuntime(session: Session): string {
  const req = session.requirements;
  if (!req) return "unknown";
  const want = [req.runtime, req.runtime_constraint || req.runtime_version]
    .filter(Boolean)
    .join(" ");
  return want || "unknown";
}
