type AgentStatusProps = {
  online: boolean | null;
  onStop?: () => void;
  stopping?: boolean;
};

export default function AgentStatus({ online, onStop, stopping }: AgentStatusProps) {
  const label =
    online === null ? "checking…" : online ? "laptop linked" : "laptop not linked";
  const color =
    online === null ? "text-slate-500" : online ? "text-moss" : "text-pollen";
  const dot =
    online === null ? "bg-slate-500" : online ? "bg-moss" : "bg-pollen";

  return (
    <div className="flex items-center gap-3">
      <div className={`flex items-center gap-2 font-mono text-xs ${color}`}>
        <span className={`inline-block h-2 w-2 rounded-full ${dot}`} />
        {label}
      </div>
      {online && onStop ? (
        <button
          type="button"
          onClick={onStop}
          disabled={stopping}
          className="border border-ink-700 px-2 py-1 font-mono text-[10px] uppercase tracking-wider text-slate-400 hover:border-rust hover:text-rust disabled:opacity-40"
        >
          {stopping ? "Stopping…" : "Stop agent"}
        </button>
      ) : null}
    </div>
  );
}
