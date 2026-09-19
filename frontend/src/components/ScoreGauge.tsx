type ScoreGaugeProps = {
  percent: number | null;
  label: string;
};

function tone(percent: number | null) {
  if (percent == null) return { stroke: "#3d4a5c", text: "text-slate-400" };
  if (percent >= 90) return { stroke: "#7cffb2", text: "text-moss" };
  if (percent >= 70) return { stroke: "#f5c44e", text: "text-pollen" };
  return { stroke: "#ff6a4a", text: "text-rust" };
}

export default function ScoreGauge({ percent, label }: ScoreGaugeProps) {
  const r = 88;
  const c = 2 * Math.PI * r;
  const shown = percent ?? 0;
  const offset = c - (shown / 100) * c;
  const colors = tone(percent);

  return (
    <div className="flex flex-col items-center">
      <div className="relative">
        <svg width="240" height="240" viewBox="0 0 240 240" aria-label={label}>
          <circle
            cx="120"
            cy="120"
            r={r}
            fill="none"
            stroke="#1c2433"
            strokeWidth="14"
          />
          <circle
            cx="120"
            cy="120"
            r={r}
            fill="none"
            stroke={colors.stroke}
            strokeWidth="14"
            strokeLinecap="round"
            strokeDasharray={c}
            strokeDashoffset={offset}
            transform="rotate(-90 120 120)"
            style={{ transition: "stroke-dashoffset 700ms ease, stroke 400ms ease" }}
          />
        </svg>
        <div className="absolute inset-0 flex flex-col items-center justify-center">
          <div className={`font-display text-7xl leading-none tracking-tight ${colors.text}`}>
            {percent == null ? "—" : percent}
          </div>
          <div className="mt-1 font-mono text-xs uppercase tracking-[0.22em] text-slate-500">
            {percent == null ? "waiting" : "% ready"}
          </div>
        </div>
      </div>
      <p className="mt-5 max-w-md text-center font-display text-xl leading-snug text-paper">
        {label}
      </p>
    </div>
  );
}
