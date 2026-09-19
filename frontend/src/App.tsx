import { FormEvent, useEffect, useMemo, useState, type ReactNode } from "react";
import AgentStatus from "./components/AgentStatus";
import BlockerList from "./components/BlockerList";
import CompatTable from "./components/CompatTable";
import CrashTimeline from "./components/CrashTimeline";
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

const EXAMPLES = [
  "https://github.com/pallets/flask",
  "https://github.com/expressjs/express",
  "https://github.com/tiangolo/fastapi",
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

  // Auto-link when connect.cmd opens Amplify with ?code=
  useEffect(() => {
    const params = new URLSearchParams(window.location.search);
    const code = (params.get("code") || "").trim().toLowerCase();
    if (code.length >= 4) {
      saveAgentCode(code);
      setAgentCodeInput(code);
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

  return (
    <div className="min-h-screen bg-ink-950">
      <header className="flex flex-wrap items-end justify-between gap-4 border-b border-ink-700 px-6 py-5 md:px-10">
        <div>
          <p className="font-mono text-[11px] uppercase tracking-[0.28em] text-moss">RepoReady</p>
          <h1 className="font-display text-2xl text-paper md:text-3xl">
            Will this repo run on your laptop?
          </h1>
          <p className="mt-1 max-w-xl text-sm text-slate-400">
            Compare what the project needs vs what you have installed — before you waste hours on
            setup.
          </p>
        </div>
        <div className="flex flex-col items-end gap-2">
          <p className="font-mono text-[11px] uppercase tracking-wider text-slate-500">
            {bedrockOn ? "AI scoring on" : "heuristic scoring"}
          </p>
          <AgentStatus online={agentOnline} onStop={onStopAgent} stopping={stoppingAgent} />
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
                Paste a public GitHub repo
              </label>
              <p className="mt-2 text-sm text-slate-400">
                We infer runtime &amp; deps (even without a README), then show what to install on{" "}
                <em className="text-paper">this</em> PC. Nothing installs until you approve.
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
                  {busy ? "Checking…" : "Check my laptop"}
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
                Path to a folder on this PC
              </label>
              <p className="mt-2 text-sm text-slate-400">
                Your linked laptop agent reads that folder locally — the cloud never sees your full
                source tree beyond setup manifests.
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
              <details className="mt-3">
                <summary className="cursor-pointer font-mono text-[11px] text-slate-500">
                  Prefer CLI?
                </summary>
                <p className="mt-2 break-all font-mono text-[11px] text-moss">
                  python agent/setup_check.py --path &quot;{localPath || "."}&quot; --api {API_BASE}
                </p>
              </details>
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
                  Laptop linked
                </p>
                <p className="mt-2">
                  Checks run on your machine. Use <em className="text-paper">Stop agent</em> in the
                  header when you&apos;re done — no need to touch the terminal.
                </p>
                {liveFingerprint ? (
                  <dl className="mt-3 grid grid-cols-2 gap-2 font-mono text-[11px] text-slate-400">
                    <div>
                      <dt className="text-slate-600">Python</dt>
                      <dd className="truncate text-paper">{liveFingerprint.python || "—"}</dd>
                    </div>
                    <div>
                      <dt className="text-slate-600">Node</dt>
                      <dd className="truncate text-paper">{liveFingerprint.node || "—"}</dd>
                    </div>
                    <div>
                      <dt className="text-slate-600">git</dt>
                      <dd className="truncate text-paper">{liveFingerprint.git || "—"}</dd>
                    </div>
                    <div>
                      <dt className="text-slate-600">OS</dt>
                      <dd className="truncate text-paper">
                        {liveFingerprint.os || "—"} {liveFingerprint.arch || ""}
                      </dd>
                    </div>
                  </dl>
                ) : (
                  <p className="mt-2 font-mono text-[11px] text-pollen">
                    Waiting for PC tool report from agent…
                  </p>
                )}
              </>
            ) : (
              <AgentBootstrap
                agentCodeInput={agentCodeInput}
                setAgentCodeInput={setAgentCodeInput}
                onCodeSaved={() => {
                  /* probe effect depends on agentCodeInput */
                }}
              />
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
                Project needs
                {session.requirements.inferred ? " · inferred from source" : ""}
                {session.requirements.notes?.some((n) => n.includes("RepoAnalystAgent"))
                  ? " · AI"
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
      <p className="font-mono text-[11px] uppercase tracking-[0.2em] text-pollen">
        Step 1 — link this laptop (agent code)
      </p>
      <ol className="mt-3 list-decimal space-y-2 pl-5 text-sm text-slate-300">
        <li>
          Copy the PowerShell command below (uses your agent code{" "}
          <span className="font-mono text-moss">{code || "……"}</span>)
        </li>
        <li>Paste it in PowerShell and press Enter — leave that window open</li>
        <li>Status flips to <em className="text-paper">laptop linked</em> automatically</li>
      </ol>

      <div className="mt-4 flex flex-wrap items-center gap-2">
        <label className="font-mono text-[11px] uppercase tracking-wider text-slate-500">
          Agent code
        </label>
        <input
          value={agentCodeInput}
          onChange={(e) => setAgentCodeInput(e.target.value.trim().toLowerCase())}
          placeholder="click Generate"
          className="w-36 border border-ink-700 bg-ink-950 px-3 py-2 font-mono text-sm text-paper outline-none focus:border-moss"
        />
        <button
          type="button"
          onClick={() => {
            const next = generateAgentCode();
            setAgentCodeInput(next);
            saveAgentCode(next);
            onCodeSaved();
          }}
          className="border border-ink-700 px-3 py-2 font-mono text-[11px] uppercase tracking-wider text-slate-300 hover:text-paper"
        >
          Generate
        </button>
        <button
          type="button"
          onClick={saveCode}
          disabled={agentCodeInput.length < 4}
          className="border border-moss px-3 py-2 font-mono text-[11px] uppercase tracking-wider text-moss disabled:opacity-40"
        >
          Use this code
        </button>
      </div>

      <pre className="mt-3 overflow-x-auto border border-ink-700 bg-ink-950 p-3 font-mono text-[11px] leading-relaxed text-slate-300">
        {command || "Generate or enter an agent code first"}
      </pre>
      <button
        type="button"
        onClick={copyCommand}
        className="mt-3 bg-moss px-4 py-3 font-mono text-xs uppercase tracking-[0.16em] text-ink-950"
      >
        {copied ? "Copied — paste in PowerShell" : "Copy PowerShell command"}
      </button>
      <p className="mt-3 font-mono text-[10px] text-slate-500">
        Prefer not to download .cmd files (Smart App Control often blocks them). Stop the agent
        anytime with <span className="text-paper">Stop agent</span> in the top-right.
      </p>

      <details className="mt-4">
        <summary className="cursor-pointer font-mono text-[11px] text-slate-500">
          Alternate: download connector .cmd (may be blocked)
        </summary>
        <a
          href={downloadUrl}
          className="mt-3 inline-block border border-ink-700 px-4 py-2 font-mono text-[11px] uppercase tracking-wider text-slate-300 hover:text-paper"
        >
          Download RepoReady-Connect.cmd
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
