import { useMemo, useState } from "react";
import type { Session } from "../types";

type AgentCommandProps = {
  session: Session;
  apiBase: string;
};

export default function AgentCommand({ session, apiBase }: AgentCommandProps) {
  const windows = useMemo(() => /Win/i.test(navigator.userAgent), []);
  const [copied, setCopied] = useState<string | null>(null);

  const oneLiner = windows
    ? session.agent_command?.windows || `irm ${apiBase}/agent.py | python - ${session.session_id}`
    : session.agent_command?.posix || `curl -fsSL ${apiBase}/agent.py | python3 - ${session.session_id}`;

  const local = session.agent_command?.local || `python agent/setup_check.py ${session.session_id} --api ${apiBase}`;

  async function copy(text: string, key: string) {
    await navigator.clipboard.writeText(text);
    setCopied(key);
    window.setTimeout(() => setCopied(null), 1400);
  }

  return (
    <div className="border border-ink-700 bg-ink-900 p-5">
      <p className="font-mono text-[11px] uppercase tracking-[0.2em] text-slate-500">
        Run on your laptop — not the AWS server
      </p>
      <p className="mt-2 text-sm text-slate-300">
        The cloud backend never sees this machine. Paste the command in a terminal on the computer that will actually run the repo.
      </p>
      <CommandBlock
        label={windows ? "PowerShell" : "Terminal"}
        command={oneLiner}
        copied={copied === "one"}
        onCopy={() => copy(oneLiner, "one")}
      />
      <CommandBlock
        label="From this repo (local dev)"
        command={local}
        copied={copied === "local"}
        onCopy={() => copy(local, "local")}
      />
    </div>
  );
}

function CommandBlock({
  label,
  command,
  copied,
  onCopy,
}: {
  label: string;
  command: string;
  copied: boolean;
  onCopy: () => void;
}) {
  return (
    <div className="mt-4">
      <div className="mb-1 flex items-center justify-between">
        <span className="font-mono text-[11px] uppercase tracking-wider text-slate-500">{label}</span>
        <button
          type="button"
          onClick={onCopy}
          className="font-mono text-[11px] uppercase tracking-wider text-moss hover:text-paper"
        >
          {copied ? "copied" : "copy"}
        </button>
      </div>
      <pre className="overflow-x-auto bg-ink-950 p-3 font-mono text-xs leading-relaxed text-moss">{command}</pre>
    </div>
  );
}
