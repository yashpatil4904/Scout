import { useState } from "react";
import type { Blocker } from "../types";

const SEVERITY: Record<string, string> = {
  critical: "border-rust text-rust",
  warning: "border-pollen text-pollen",
  info: "border-slate-500 text-slate-400",
};

const ACTIONABLE = new Set([
  "install-unverified",
  "install-failed",
  "boot-unverified",
  "boot-failed",
  "python-missing",
  "python-mismatch",
  "node-missing",
  "node-mismatch",
  "git-missing",
  "npm-missing",
  "pip-missing",
  "yarn-missing",
  "pnpm-missing",
  "docker-missing",
  "svc-postgres",
  "svc-redis",
  "svc-mongodb",
  "svc-mysql",
]);

function isActionable(id: string) {
  return ACTIONABLE.has(id) || id.startsWith("svc-");
}

function actionLabel(id: string): string {
  if (id === "boot-unverified" || id === "boot-failed") return "Boot";
  if (id.startsWith("svc-")) return "Start service";
  if (id === "install-unverified" || id === "install-failed") return "Install deps";
  return "Install";
}

type BlockerListProps = {
  blockers: Blocker[];
  sessionId?: string;
  onApprove?: (blockerId: string) => Promise<void>;
};

export default function BlockerList({ blockers, sessionId, onApprove }: BlockerListProps) {
  const problems = blockers.filter((b) => b.severity !== "info");
  const optional = blockers.filter((b) => b.severity === "info");

  if (!blockers.length) {
    return (
      <p className="font-mono text-sm text-slate-400">
        Nothing to install — this PC already matches the repo.
      </p>
    );
  }
  const software = problems.filter((b) => b.id !== "config-env" && !b.id.startsWith("env-"));
  const config = problems.filter((b) => b.id === "config-env" || b.id.startsWith("env-"));

  return (
    <div className="flex flex-col gap-6">
      {software.length ? (
        <div>
          <p className="mb-3 font-mono text-[11px] uppercase tracking-[0.2em] text-slate-500">
            Install on this PC
          </p>
          <ol className="flex flex-col gap-3">
            {software.map((b, i) => (
              <BlockerRow
                key={b.id}
                blocker={b}
                index={i + 1}
                sessionId={sessionId}
                onApprove={onApprove}
              />
            ))}
          </ol>
        </div>
      ) : (
        <p className="font-mono text-sm text-moss">
          Runtime and tools look good on this machine.
        </p>
      )}
      {config.length ? (
        <div>
          <p className="mb-3 font-mono text-[11px] uppercase tracking-[0.2em] text-slate-500">
            Config (not software)
          </p>
          <ol className="flex flex-col gap-3">
            {config.map((b, i) => (
              <BlockerRow
                key={b.id}
                blocker={b}
                index={i + 1}
                sessionId={sessionId}
                onApprove={onApprove}
              />
            ))}
          </ol>
        </div>
      ) : null}
      {optional.length ? (
        <div>
          <p className="mb-3 font-mono text-[11px] uppercase tracking-[0.2em] text-slate-500">
            Optional proof (not failures)
          </p>
          <ol className="flex flex-col gap-3">
            {optional.map((b, i) => (
              <BlockerRow
                key={b.id}
                blocker={b}
                index={i + 1}
                sessionId={sessionId}
                onApprove={onApprove}
              />
            ))}
          </ol>
        </div>
      ) : null}
    </div>
  );
}

function BlockerRow({
  blocker,
  index,
  sessionId,
  onApprove,
}: {
  blocker: Blocker;
  index: number;
  sessionId?: string;
  onApprove?: (blockerId: string) => Promise<void>;
}) {
  const [copied, setCopied] = useState(false);
  const [status, setStatus] = useState<"idle" | "confirm" | "installing" | "skipped" | "done">(
    "idle",
  );
  const tone = SEVERITY[blocker.severity] || SEVERITY.info;
  const canInstall = Boolean(sessionId && onApprove && isActionable(blocker.id));
  const primaryLabel = actionLabel(blocker.id);

  async function copyFix() {
    await navigator.clipboard.writeText(blocker.fix);
    setCopied(true);
    window.setTimeout(() => setCopied(false), 1400);
  }

  async function confirmInstall() {
    if (!onApprove) return;
    setStatus("installing");
    try {
      await onApprove(blocker.id);
      setStatus("done");
    } catch {
      setStatus("idle");
    }
  }

  return (
    <li className="border border-ink-700 bg-ink-900 p-4">
      <div className="flex items-start justify-between gap-3">
        <div>
          <div className="flex items-center gap-2">
            <span className="font-mono text-xs text-slate-500">
              {String(index).padStart(2, "0")}
            </span>
            <span
              className={`border px-1.5 py-0.5 font-mono text-[10px] uppercase tracking-wider ${tone}`}
            >
              {blocker.severity}
            </span>
          </div>
          <h3 className="mt-2 font-sans text-base font-medium text-paper">{blocker.title}</h3>
          {blocker.evidence ? (
            <p className="mt-1 font-mono text-xs leading-relaxed text-slate-500">
              {blocker.evidence}
            </p>
          ) : null}
        </div>
      </div>
      <div className="mt-3 flex items-start justify-between gap-3 border-t border-ink-700 pt-3">
        <p className="font-mono text-xs leading-relaxed text-moss">{blocker.fix}</p>
        <button
          type="button"
          onClick={copyFix}
          className="shrink-0 font-mono text-[11px] uppercase tracking-wider text-slate-400 hover:text-paper"
        >
          {copied ? "copied" : "copy fix"}
        </button>
      </div>

      {canInstall && status === "idle" ? (
        <div className="mt-3 flex flex-wrap gap-2">
          <button
            type="button"
            onClick={() => setStatus("confirm")}
            className="bg-moss px-3 py-1.5 font-mono text-[11px] uppercase tracking-wider text-ink-950"
          >
            {primaryLabel}
          </button>
          <button
            type="button"
            onClick={() => setStatus("skipped")}
            className="border border-ink-700 px-3 py-1.5 font-mono text-[11px] uppercase tracking-wider text-slate-400 hover:text-paper"
          >
            Skip
          </button>
        </div>
      ) : null}

      {status === "confirm" ? (
        <div className="mt-3 border border-pollen bg-ink-950 p-3">
          <p className="font-mono text-xs text-pollen">
            Run this on your laptop now? Nothing happens until you confirm.
          </p>
          <p className="mt-2 font-mono text-[11px] text-slate-400">{blocker.fix}</p>
          <div className="mt-3 flex gap-2">
            <button
              type="button"
              onClick={confirmInstall}
              className="bg-moss px-3 py-1.5 font-mono text-[11px] uppercase tracking-wider text-ink-950"
            >
              Yes, {primaryLabel.toLowerCase()}
            </button>
            <button
              type="button"
              onClick={() => setStatus("idle")}
              className="border border-ink-700 px-3 py-1.5 font-mono text-[11px] uppercase tracking-wider text-slate-400"
            >
              Cancel
            </button>
          </div>
        </div>
      ) : null}

      {status === "installing" ? (
        <p className="mt-3 font-mono text-xs text-pollen">Running on this machine…</p>
      ) : null}
      {status === "skipped" ? (
        <p className="mt-3 font-mono text-xs text-slate-500">Skipped — left as a blocker.</p>
      ) : null}
      {status === "done" ? (
        <p className="mt-3 font-mono text-xs text-moss">Approved — score will refresh shortly.</p>
      ) : null}
    </li>
  );
}
