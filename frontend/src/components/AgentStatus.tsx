type AgentStatusProps = {
  online: boolean | null;
  onStop?: () => void;
  stopping?: boolean;
};

export default function AgentStatus({ online, onStop, stopping }: AgentStatusProps) {
  const label =
    online === null ? "checking…" : online ? "laptop linked" : "laptop not linked";
  const color =
    online === null ? "text-ink-400" : online ? "text-sea" : "text-pollen";
  const dot =
    online === null
      ? "bg-ink-400"
      : online
        ? "bg-sea animate-pulse-dot"
        : "bg-pollen";

  return (
    <div className="flex items-center gap-3">
      <div className={`flex items-center gap-2 font-mono text-xs ${color}`}>
        <span className={`inline-block h-2 w-2 ${dot}`} />
        {label}
      </div>
      {online && onStop ? (
        <button
          type="button"
          onClick={onStop}
          disabled={stopping}
          className="font-mono text-[10px] uppercase tracking-wider text-ink-400 underline-offset-2 hover:text-rust hover:underline disabled:opacity-40"
        >
          {stopping ? "Stopping…" : "Stop agent"}
        </button>
      ) : null}
    </div>
  );
}
