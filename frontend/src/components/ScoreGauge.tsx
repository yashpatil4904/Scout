type ScoreGaugeProps = {
  percent: number | null;
  label: string;
};

function tone(percent: number | null) {
  if (percent == null) return { stroke: "#c5ced9", text: "text-ink-400" };
  if (percent >= 90) return { stroke: "#0f766e", text: "text-sea" };
  if (percent >= 70) return { stroke: "#c47f0a", text: "text-pollen" };
  return { stroke: "#dc4a3d", text: "text-rust" };
}

export default function ScoreGauge({ percent, label }: ScoreGaugeProps) {
  const r = 88;
  const c = 2 * Math.PI * r;
  const shown = percent ?? 0;
  const offset = c - (shown / 100) * c;
  const colors = tone(percent);

  return (
    <div className="flex flex-col items-center lg:items-start">
      <div className="relative">
        <svg width="220" height="220" viewBox="0 0 240 240" aria-label={label}>
          <circle cx="120" cy="120" r={r} fill="none" stroke="#dde3eb" strokeWidth="12" />
          <circle
            cx="120"
            cy="120"
            r={r}
            fill="none"
            stroke={colors.stroke}
            strokeWidth="12"
            strokeLinecap="round"
            strokeDasharray={c}
            strokeDashoffset={offset}
            transform="rotate(-90 120 120)"
            style={{ transition: "stroke-dashoffset 700ms ease, stroke 400ms ease" }}
          />
        </svg>
        <div className="absolute inset-0 flex flex-col items-center justify-center">
          <div className={`font-display text-6xl font-bold leading-none tracking-tight ${colors.text}`}>
            {percent == null ? "—" : percent}
          </div>
          <div className="mt-1 font-mono text-[10px] uppercase tracking-[0.22em] text-ink-400">
            {percent == null ? "waiting" : "% ready"}
          </div>
        </div>
      </div>
      <p className="mt-4 max-w-md text-center font-sans text-base leading-snug text-ink-800 lg:text-left">
        {label}
      </p>
    </div>
  );
}
