type AgentStatusProps = {
  online: boolean | null;
};

export default function AgentStatus({ online }: AgentStatusProps) {
  const label =
    online === null ? "checking agent…" : online ? "agent online" : "agent offline";
  const color =
    online === null ? "text-slate-500" : online ? "text-moss" : "text-pollen";
  const dot =
    online === null ? "bg-slate-500" : online ? "bg-moss" : "bg-pollen";

  return (
    <div className={`flex items-center gap-2 font-mono text-xs ${color}`}>
      <span className={`inline-block h-2 w-2 rounded-full ${dot}`} />
      {label}
    </div>
  );
}
