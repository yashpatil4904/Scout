import type { Fingerprint, Requirements } from "../types";

type Row = {
  need: string;
  have: string;
  status: "ok" | "gap" | "warn" | "na";
};

function major(ver: string | null | undefined): number | null {
  if (!ver) return null;
  const m = String(ver).replace(/^v/i, "").match(/(\d+)/);
  return m ? int(m[1]) : null;
}

function int(s: string): number {
  return parseInt(s, 10);
}

function nodeOk(need: string | null | undefined, have: string | null | undefined): boolean {
  const n = major(need);
  const h = major(have);
  if (n == null) return Boolean(have);
  if (h == null) return false;
  return h >= n;
}

function pythonOk(need: string | null | undefined, have: string | null | undefined): boolean {
  if (!need) return Boolean(have);
  if (!have) return false;
  const nm = String(need).match(/(\d+)\.(\d+)/);
  const hm = String(have).match(/(\d+)\.(\d+)/);
  if (!nm || !hm) return have.includes(need);
  const [na, nb] = [int(nm[1]), int(nm[2])];
  const [ha, hb] = [int(hm[1]), int(hm[2])];
  if (ha !== na) return ha > na;
  return hb >= nb;
}

function buildRows(req: Requirements, fp: Fingerprint): Row[] {
  const rows: Row[] = [];
  const runtime = req.runtime || "unknown";
  const needVer = req.runtime_constraint || req.runtime_version || "any";

  if (runtime === "python") {
    const ok = pythonOk(req.runtime_version, fp.python);
    rows.push({
      need: `Python ${needVer}`,
      have: fp.python || "not installed",
      status: fp.python ? (ok ? "ok" : "gap") : "gap",
    });
    rows.push({
      need: "pip (or poetry/uv)",
      have: fp.tools?.find((t) => /pip|poetry|uv|conda/i.test(t)) || (fp.python ? "python present" : "missing"),
      status: fp.python ? "ok" : "gap",
    });
  } else if (runtime === "node") {
    const ok = nodeOk(req.runtime_version, fp.node);
    rows.push({
      need: `Node.js ${needVer}`,
      have: fp.node || "not installed",
      status: fp.node ? (ok ? "ok" : "gap") : "gap",
    });
    rows.push({
      need: req.package_manager || "npm",
      have: fp.npm || fp.tools?.find((t) => /npm|yarn|pnpm/i.test(t)) || (fp.node ? "via node" : "missing"),
      status: fp.node || fp.npm ? "ok" : "gap",
    });
  } else {
    rows.push({
      need: `Runtime: ${runtime}`,
      have: fp.python || fp.node || "unknown",
      status: "warn",
    });
  }

  rows.push({
    need: "git",
    have: fp.git || "missing",
    status: fp.git ? "ok" : "gap",
  });

  if (req.services?.length) {
    for (const svc of req.services) {
      const running = fp.services_running?.includes(svc);
      rows.push({
        need: `Service: ${svc}`,
        have: running ? "running" : "not detected",
        status: running ? "ok" : "gap",
      });
    }
  }

  if (req.env_vars?.length) {
    const missing = fp.env_vars_missing?.length
      ? fp.env_vars_missing
      : req.env_vars.filter((e) => !(fp.env_vars_present || []).includes(e));
    rows.push({
      need: `Env config (${req.env_vars.length})`,
      have: missing.length ? `${missing.length} missing` : "present",
      status: missing.length ? "warn" : "ok",
    });
  }

  if (fp.ram_mb) {
    const gb = (fp.ram_mb / 1024).toFixed(0);
    rows.push({
      need: "RAM (this PC)",
      have: `${gb} GB`,
      status: fp.ram_mb < 4096 ? "warn" : "ok",
    });
  }

  return rows;
}

const TONE: Record<Row["status"], string> = {
  ok: "text-sea",
  gap: "text-rust",
  warn: "text-pollen",
  na: "text-ink-400",
};

const LABEL: Record<Row["status"], string> = {
  ok: "OK",
  gap: "INSTALL",
  warn: "CONFIG",
  na: "—",
};

type Props = {
  requirements: Requirements;
  fingerprint: Fingerprint;
};

export default function CompatTable({ requirements, fingerprint }: Props) {
  const empty =
    !fingerprint?.python &&
    !fingerprint?.node &&
    !fingerprint?.git &&
    (!fingerprint?.os || fingerprint.os === "unknown");
  if (empty) {
    return (
      <div className="border-l-2 border-pollen pl-4">
        <p className="section-label text-pollen">Your PC vs this repo</p>
        <p className="mt-2 text-sm text-ink-600">
          No laptop fingerprint yet — keep the agent window open and run the check again.
        </p>
      </div>
    );
  }
  const rows = buildRows(requirements, fingerprint);
  return (
    <div>
      <p className="section-label">Your PC vs this repo</p>
      <p className="mt-2 text-sm text-ink-700">
        Software gaps first. Env vars are config — not winget installs.
      </p>
      <div className="mt-4 overflow-x-auto">
        <table className="w-full border-collapse font-mono text-xs">
          <thead>
            <tr className="border-b border-[#d0d7e0] text-left text-[10px] uppercase tracking-wider text-ink-600">
              <th className="pb-2 pr-3 font-medium">Repo needs</th>
              <th className="pb-2 pr-3 font-medium">This PC has</th>
              <th className="pb-2 font-medium">Status</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((r) => (
              <tr key={`${r.need}-${r.have}`} className="border-b border-[#e8ecf1]">
                <td className="py-2.5 pr-3 text-ink-700">{r.need}</td>
                <td className="py-2.5 pr-3 font-medium text-ink-900">{r.have}</td>
                <td className={`py-2.5 font-semibold ${TONE[r.status]}`}>{LABEL[r.status]}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}
