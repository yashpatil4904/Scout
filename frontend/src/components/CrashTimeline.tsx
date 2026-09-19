import type { CrashFrame, CrashPreview } from "../types";

const TONE: Record<string, string> = {
  ok: "text-sea",
  fail: "text-rust",
  warn: "text-pollen",
  skip: "text-ink-400",
};

const BADGE: Record<string, string> = {
  ok: "OK",
  fail: "CRASH",
  warn: "WARN",
  skip: "SKIP",
};

type Props = {
  preview: CrashPreview;
};

export default function CrashTimeline({ preview }: Props) {
  const frames = preview.frames || [];
  if (!frames.length) return null;

  return (
    <div>
      <p className="section-label">Future-crash preview</p>
      <p className="mt-2 text-sm leading-relaxed text-ink-700">
        {preview.summary || "What will break first if you run this on this PC now."}
      </p>
      {preview.entrypoint ? (
        <p className="mt-2 font-mono text-[11px] text-ink-600">
          entry → <span className="font-medium text-sea">{preview.entrypoint}</span>
        </p>
      ) : null}

      <ol className="mt-6 flex flex-col">
        {frames.map((fr, i) => (
          <FrameRow key={`${fr.step}-${fr.title}`} frame={fr} last={i === frames.length - 1} />
        ))}
      </ol>
    </div>
  );
}

function FrameRow({ frame, last }: { frame: CrashFrame; last: boolean }) {
  const tone = TONE[frame.status] || TONE.skip;
  return (
    <li className="relative flex gap-4 pb-5">
      {!last ? (
        <span
          className="absolute left-[11px] top-7 h-[calc(100%-12px)] w-px bg-ink-900/10"
          aria-hidden
        />
      ) : null}
      <span
        className={`relative z-[1] mt-0.5 flex h-6 w-6 shrink-0 items-center justify-center bg-sand-100 font-mono text-[10px] font-medium ${tone}`}
      >
        {frame.step}
      </span>
      <div className="min-w-0 flex-1">
        <div className="flex flex-wrap items-center gap-2">
          <span className={`font-mono text-[10px] uppercase tracking-wider ${tone}`}>
            {BADGE[frame.status] || frame.status}
          </span>
          <h3 className="font-display text-sm font-semibold text-ink-900">{frame.title}</h3>
        </div>
        {frame.detail ? (
          <p className="mt-1 font-mono text-[11px] leading-relaxed text-ink-600">{frame.detail}</p>
        ) : null}
        {frame.would_see && frame.status === "fail" ? (
          <pre className="mt-2 overflow-x-auto border border-rust/30 bg-[#fff5f4] px-3 py-2 font-mono text-[11px] text-rust">
            {frame.would_see}
          </pre>
        ) : null}
        {frame.fix && frame.status !== "ok" && frame.status !== "skip" ? (
          <p className="mt-2 font-mono text-[11px] text-sea">{frame.fix}</p>
        ) : null}
      </div>
    </li>
  );
}
