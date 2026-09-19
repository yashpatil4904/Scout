type LandingProps = {
  onStart: () => void;
};

export default function Landing({ onStart }: LandingProps) {
  return (
    <div className="min-h-screen bg-[#f4f5f7]">
      <section className="relative flex min-h-[100svh] flex-col bg-white">
        <nav className="relative z-10 flex items-center justify-between border-b border-[#d0d7e0] px-6 py-5 md:px-12">
          <span className="font-display text-lg font-bold tracking-tight text-ink-900">Scout</span>
          <button type="button" onClick={onStart} className="btn-ghost text-xs md:text-sm">
            Open checker
          </button>
        </nav>

        {/* Two clear columns — never overlap */}
        <div className="relative z-10 mx-auto grid w-full max-w-6xl flex-1 grid-cols-1 content-center gap-12 px-6 pb-16 pt-6 md:px-12 lg:grid-cols-2 lg:items-center lg:gap-16 lg:pb-24">
          <div className="min-w-0">
            <p className="animate-fade-up font-display text-6xl font-extrabold leading-none tracking-tight text-ink-900 sm:text-7xl md:text-8xl">
              Scout
            </p>
            <h1 className="animate-fade-up-delay mt-6 max-w-md font-display text-2xl font-semibold leading-snug tracking-tight text-ink-800 sm:text-3xl">
              Will this repo run on your laptop?
            </h1>
            <p className="animate-fade-up-late mt-4 max-w-md text-base leading-relaxed text-ink-700 sm:text-lg">
              Compare what the project needs to what you already have installed — before you burn an
              evening on setup.
            </p>
            <div className="animate-fade-up-late mt-8 flex flex-wrap items-center gap-3">
              <button type="button" onClick={onStart} className="btn-primary">
                Check a project
              </button>
              <a
                href="#how"
                className="font-sans text-sm font-medium text-ink-500 underline-offset-4 hover:text-sea hover:underline"
              >
                How it works
              </a>
            </div>
          </div>

          <div className="animate-drift w-full min-w-0 max-w-md justify-self-start lg:justify-self-end" aria-hidden>
            <div className="relative overflow-hidden bg-ink-900 px-6 py-8 text-sand-50 shadow-lift">
              <div className="absolute inset-0 bg-[radial-gradient(ellipse_at_top_right,rgba(15,118,110,0.35),transparent_55%)]" />
              <div className="relative">
                <p className="font-mono text-[10px] uppercase tracking-[0.28em] text-sea-bright">
                  this laptop
                </p>
                <p className="mt-6 font-display text-7xl font-bold tabular-nums leading-none text-white">
                  78
                  <span className="ml-1 text-2xl font-semibold text-sea-bright">%</span>
                </p>
                <p className="mt-2 font-mono text-xs text-sand-300">ready to run</p>
                <div className="mt-8 space-y-3 border-t border-white/10 pt-6 font-mono text-[11px]">
                  <Row ok label="Python ≥3.11" value="3.12.10" />
                  <Row ok label="Node ≥18" value="v24.19.0" />
                  <Row warn label="Redis" value="not running" />
                </div>
              </div>
            </div>
          </div>
        </div>
      </section>

      <section id="how" className="border-t border-ink-900/10 bg-white/50 px-6 py-20 md:px-12">
        <div className="mx-auto max-w-5xl">
          <p className="section-label">How it works</p>
          <h2 className="mt-3 max-w-2xl font-display text-3xl font-bold tracking-tight text-ink-900 md:text-4xl">
            Cloud reads the project. Your laptop reports what&apos;s installed.
          </h2>
          <ol className="mt-12 grid gap-10 md:grid-cols-3 md:gap-8">
            <Step
              n="01"
              title="Link this laptop"
              body="A tiny agent runs locally and shares an agent code with the site — browsers can’t read your PC from HTTPS alone."
            />
            <Step
              n="02"
              title="Paste a repo or path"
              body="Public GitHub URL, or a folder already on disk. We infer runtime and deps even when the README is thin."
            />
            <Step
              n="03"
              title="See what’s missing"
              body="One readiness score, a crash-order timeline, and install steps. Nothing installs until you approve."
            />
          </ol>
        </div>
      </section>

      <section className="border-t border-ink-900/10 px-6 py-20 md:px-12">
        <div className="mx-auto grid max-w-5xl gap-12 lg:grid-cols-2 lg:gap-20">
          <div>
            <p className="section-label">Why it exists</p>
            <h2 className="mt-3 font-display text-3xl font-bold tracking-tight text-ink-900">
              CI is green. Your machine isn&apos;t.
            </h2>
            <p className="mt-4 text-base leading-relaxed text-ink-500">
              READMEs assume the author&apos;s laptop. Containers assume Docker. Chatbots guess from
              text. Scout measures <em className="text-ink-800">this</em> PC against{" "}
              <em className="text-ink-800">this</em> project — the gap that actually burns evenings.
            </p>
          </div>
          <ul className="space-y-6 self-center">
            <Bullet title="GitHub or local folder" body="Clone check or the project you’re in now." />
            <Bullet
              title="Future-crash preview"
              body="Ordered failures: runtime → install → env → boot."
            />
            <Bullet
              title="Approve to install"
              body="Fingerprint first. pip/npm/winget only when you say yes."
            />
          </ul>
        </div>
      </section>

      <footer className="border-t border-ink-900/10 px-6 py-10 md:px-12">
        <div className="mx-auto flex max-w-5xl flex-col items-start justify-between gap-6 sm:flex-row sm:items-center">
          <div>
            <p className="font-display text-xl font-bold text-ink-900">Scout</p>
            <p className="mt-1 text-sm text-ink-500">Setup readiness for developers.</p>
          </div>
          <button type="button" onClick={onStart} className="btn-primary">
            Start checking
          </button>
        </div>
      </footer>
    </div>
  );
}

function Step({ n, title, body }: { n: string; title: string; body: string }) {
  return (
    <li>
      <p className="font-mono text-xs text-sea">{n}</p>
      <h3 className="mt-2 font-display text-xl font-bold text-ink-900">{title}</h3>
      <p className="mt-2 text-sm leading-relaxed text-ink-500">{body}</p>
    </li>
  );
}

function Bullet({ title, body }: { title: string; body: string }) {
  return (
    <li className="border-l-2 border-sea pl-4">
      <p className="font-display text-lg font-semibold text-ink-900">{title}</p>
      <p className="mt-1 text-sm text-ink-500">{body}</p>
    </li>
  );
}

function Row({
  ok,
  warn,
  label,
  value,
}: {
  ok?: boolean;
  warn?: boolean;
  label: string;
  value: string;
}) {
  return (
    <div className="flex items-center justify-between gap-4">
      <span className="text-sand-300">{label}</span>
      <span className={ok ? "text-sea-bright" : warn ? "text-pollen" : "text-white"}>{value}</span>
    </div>
  );
}
