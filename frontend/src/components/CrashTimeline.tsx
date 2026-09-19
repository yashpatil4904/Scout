import type { CrashFrame, CrashPreview } from "../types";

const TONE: Record<string, string> = {
  ok: "border-moss text-moss",
  fail: "border-rust text-rust",
  warn: "border-pollen text-pollen",
  skip: "border-slate-600 text-slate-500",
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
    <div className="border border-ink-700 bg-ink-900 p-5">
      <p className="font-mono text-[11px] uppercase tracking-[0.2em] text-slate-500">
        Future-crash preview
      </p>
      <p className="mt-1 text-sm text-slate-300">
        {preview.summary || "What will break first if you run this on this PC now."}
      </p>
      {preview.entrypoint ? (
        <p className="mt-2 font-mono text-[11px] text-slate-500">
          entry → <span className="text-moss">{preview.entrypoint}</span>
        </p>
      ) : null}

      <ol className="mt-4 flex flex-col gap-0">
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
    <li className="relative flex gap-3 pb-4">
      {!last ? (
        <span className="absolute left-[11px] top-6 h-[calc(100%-8px)] w-px bg-ink-700" aria-hidden />
      ) : null}
      <span
        className={`relative z-[1] mt-0.5 flex h-6 w-6 shrink-0 items-center justify-center border bg-ink-950 font-mono text-[10px] ${tone}`}
      >
        {frame.step}
      </span>
      <div className="min-w-0 flex-1">
        <div className="flex flex-wrap items-center gap-2">
          <span className={`border px-1.5 py-0.5 font-mono text-[10px] uppercase tracking-wider ${tone}`}>
            {BADGE[frame.status] || frame.status}
          </span>
          <h3 className="font-sans text-sm font-medium text-paper">{frame.title}</h3>
        </div>
        {frame.detail ? (
          <p className="mt-1 font-mono text-[11px] leading-relaxed text-slate-500">{frame.detail}</p>
        ) : null}
        {frame.would_see && frame.status === "fail" ? (
          <pre className="mt-2 overflow-x-auto border border-rust/40 bg-ink-950 p-2 font-mono text-[11px] text-rust">
            {frame.would_see}
          </pre>
        ) : null}
        {frame.fix && frame.status !== "ok" && frame.status !== "skip" ? (
          <p className="mt-2 font-mono text-[11px] text-moss">{frame.fix}</p>
        ) : null}
      </div>
    </li>
  );
}
