/** Browser folder scan — no laptop agent. Chrome/Edge File System Access API. */

const SKIP_DIRS = new Set([
  "node_modules",
  ".git",
  "dist",
  "build",
  "coverage",
  ".next",
  ".venv",
  "venv",
  "__pycache__",
  ".tox",
  ".mypy_cache",
  "target",
  ".setup-check",
]);

const MANIFEST_NAMES = new Set([
  "requirements.txt",
  "pyproject.toml",
  "Pipfile",
  "Pipfile.lock",
  "package.json",
  "package-lock.json",
  "yarn.lock",
  "pnpm-lock.yaml",
  "Dockerfile",
  "docker-compose.yml",
  "docker-compose.yaml",
  "compose.yml",
  "compose.yaml",
  ".env.example",
  ".python-version",
  ".nvmrc",
  "go.mod",
  "Cargo.toml",
  "pom.xml",
  "build.gradle",
  "manage.py",
  "README.md",
  "readme.md",
  "README.rst",
]);

const SOURCE_EXT = new Set([".py", ".js", ".ts", ".tsx", ".jsx", ".mjs", ".cjs", ".md"]);

export type FolderScan = {
  localPath: string;
  files: Record<string, string>;
  treePaths: string[];
  tools: string[];
};

function browserOs(): string {
  const ua = navigator.userAgent || "";
  if (/Windows/i.test(ua)) return "Windows";
  if (/Mac/i.test(ua)) return "macOS";
  if (/Linux/i.test(ua)) return "Linux";
  return "unknown";
}

export function browserFingerprint(extraTools: string[] = []): Record<string, unknown> {
  return {
    source: "browser",
    os: browserOs(),
    arch: (navigator as Navigator & { userAgentData?: { platform?: string } }).userAgentData
      ?.platform
      ? "auto"
      : "unknown",
    tools: extraTools,
  };
}

export function folderPickerSupported(): boolean {
  return typeof window !== "undefined" && "showDirectoryPicker" in window;
}

export async function pickAndScanFolder(): Promise<FolderScan> {
  if (!folderPickerSupported()) {
    throw new Error("Folder picker needs Chrome or Edge");
  }
  // @ts-expect-error File System Access API
  const root: FileSystemDirectoryHandle = await window.showDirectoryPicker({
    mode: "read",
  });
  const files: Record<string, string> = {};
  const treePaths: string[] = [];
  const tools: string[] = [];
  let sourceCount = 0;

  async function walk(dir: FileSystemDirectoryHandle, prefix: string, depth: number) {
    // @ts-expect-error async iterator
    for await (const [name, handle] of dir.entries()) {
      const rel = prefix ? `${prefix}/${name}` : name;
      if (handle.kind === "directory") {
        if (SKIP_DIRS.has(name) || name.startsWith(".")) {
          if (name === "node_modules" && !tools.includes("node_modules")) tools.push("node_modules");
          if ((name === ".venv" || name === "venv") && !tools.includes("venv")) tools.push("venv");
          if (treePaths.length < 2000) treePaths.push(rel);
          continue;
        }
        if (treePaths.length < 2000) treePaths.push(rel);
        if (depth < 6) await walk(handle, rel, depth + 1);
        continue;
      }
      if (treePaths.length < 2000) treePaths.push(rel);
      const lower = name.toLowerCase();
      const ext = lower.includes(".") ? `.${lower.split(".").pop()}` : "";
      const isManifest = MANIFEST_NAMES.has(name) || MANIFEST_NAMES.has(lower);
      const isSource = SOURCE_EXT.has(ext) && depth <= 3 && sourceCount < 60;
      if (!isManifest && !isSource) continue;
      try {
        const file = await handle.getFile();
        const text = await file.text();
        const capped = text.slice(0, isManifest ? 20000 : 4000);
        files[rel] = capped;
        if (!rel.includes("/")) files[name] = capped;
        if (isSource) sourceCount += 1;
      } catch {
        /* skip unreadable */
      }
    }
  }

  await walk(root, "", 0);
  return {
    localPath: root.name,
    files,
    treePaths,
    tools,
  };
}
