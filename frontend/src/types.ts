export type SessionStatus =
  | "analyzing"
  | "awaiting_agent"
  | "scoring"
  | "complete"
  | "error";

export type Blocker = {
  id: string;
  title: string;
  severity: "critical" | "warning" | "info" | string;
  evidence: string;
  fix: string;
};

export type Requirements = {
  language?: string | null;
  runtime?: string | null;
  runtime_version?: string | null;
  runtime_constraint?: string | null;
  package_manager?: string | null;
  env_vars?: string[];
  services?: string[];
  packages?: string[];
  start_command?: string | null;
  install_command?: string | null;
  health_path?: string | null;
  health_port?: number | null;
  inferred?: boolean;
  manifests_found?: string[];
  notes?: string[];
};

export type Fingerprint = {
  os?: string;
  arch?: string;
  ram_mb?: number | null;
  disk_mb?: number | null;
  python?: string | null;
  node?: string | null;
  npm?: string | null;
  git?: string | null;
  docker?: string | null;
  tools?: string[];
  env_vars_present?: string[];
  env_vars_missing?: string[];
  services_running?: string[];
  services_missing?: string[];
};

export type InstallResult = {
  attempted: boolean;
  ok?: boolean | null;
  logs?: string;
  diagnosis?: string | null;
  command?: string | null;
};

export type BootResult = {
  attempted: boolean;
  ok?: boolean | null;
  port?: number | null;
  health?: string | null;
  logs?: string;
  command?: string | null;
};

export type CrashFrame = {
  step: number;
  status: "ok" | "fail" | "warn" | "skip" | string;
  title: string;
  detail: string;
  would_see?: string;
  fix?: string;
  category?: string;
};

export type CrashPreview = {
  entrypoint?: string | null;
  summary?: string;
  predicted_fail_at?: number | null;
  engine?: string;
  frames: CrashFrame[];
};

export type Session = {
  session_id: string;
  repo_url: string;
  status: SessionStatus | string;
  owner?: string | null;
  repo?: string | null;
  source?: "github" | "local" | string;
  local_path?: string | null;
  requirements?: Requirements | null;
  fingerprint?: Fingerprint | null;
  install?: InstallResult | null;
  boot?: BootResult | null;
  crash_preview?: CrashPreview | null;
  score?: {
    percent: number;
    summary: string;
    blockers: Blocker[];
    engine?: "heuristic" | "bedrock" | string;
  } | null;
  agent_command?: {
    posix: string;
    windows: string;
    local: string;
  } | null;
  error?: string | null;
};
