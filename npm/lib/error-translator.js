const ISSUE_URL = 'https://github.com/JonathanRosado/claude-anyteam/issues/new';
const PYTHON_DOWNLOADS_URL = 'https://www.python.org/downloads/';
const CLAUDE_CODE_SETUP_URL = 'https://docs.claude.com/en/docs/claude-code/setup';

const HARD = 'hard';
const SOFT = 'soft';

const PRERELEASE_BLOCKED = /pre-release.*weren't enabled|--prerelease=allow/i;
const NO_SOLUTION = /no solution found when resolving dependencies/i;
const PYTHON_MISSING = /no.*python.*(found|interpreter)|could not find an interpreter/i;
const NETWORK = /connection.*(refused|timed?\s*out|reset)|name resolution|getaddrinfo|EAI_AGAIN|tls handshake|certificate verify failed/i;
const DISK_FULL = /no space left|ENOSPC|disk.*full/i;
const PERMISSION_DENIED = /permission denied|EACCES|access.*denied/i;
const WINDOWS_LONG_PATH = /path.*(too long|exceeds|260)|filename.*too long/i;
const WINDOWS_STORE_PYTHON = /Microsoft Store|Windows Store|python3?\.exe.*not.*found.*install Python/i;
const CLAUDE_NOT_FOUND = /claude(?: code)? cli.*not (?:detected|found)|claude.*not.*PATH|command not found: claude|ENOENT.*claude/i;

function detailsFromObject(value) {
  return value.raw ?? value.stderr ?? value.details ?? value.stdout ?? value.message ?? '';
}

function normalize(rawError, context = {}) {
  const objectInput = rawError && typeof rawError === 'object';
  const details = objectInput ? detailsFromObject(rawError) : rawError;
  return {
    text: String(details ?? ''),
    platform: context.platform ?? (objectInput ? rawError.platform : undefined) ?? process.platform,
    pythonVersion: context.pythonVersion ?? (objectInput ? rawError.pythonVersion : undefined),
    kind: context.kind ?? (objectInput ? rawError.kind : undefined),
  };
}

export function parsePythonVersion(version) {
  const match = String(version ?? '').match(/\b(?:Python\s*)?(\d+)\.(\d+)(?:\.(\d+))?\b/i);
  if (!match) {
    return null;
  }
  return {
    major: Number.parseInt(match[1], 10),
    minor: Number.parseInt(match[2], 10),
    patch: Number.parseInt(match[3] ?? '0', 10),
  };
}

export function isPythonVersionTooOld(version) {
  const parsed = parsePythonVersion(version);
  if (!parsed) {
    return false;
  }
  return parsed.major < 3 || (parsed.major === 3 && parsed.minor < 12);
}

function extractDetectedPythonVersion(text) {
  const patterns = [
    /(?:detected|found|using|current)\s+(?:Python\s*)?(\d+\.\d+(?:\.\d+)?)/i,
    /Python\s+(\d+\.\d+(?:\.\d+)?)\s+(?:is\s+)?(?:too old|unsupported|not supported)/i,
    /running\s+(?:on\s+)?Python\s+(\d+\.\d+(?:\.\d+)?)/i,
  ];
  for (const pattern of patterns) {
    const match = String(text ?? '').match(pattern);
    if (match) {
      return match[1];
    }
  }
  return null;
}

function isWindows(raw) {
  return raw.platform === 'win32';
}

function prereleaseBlocked(raw) {
  return PRERELEASE_BLOCKED.test(raw.text);
}

export const patterns = [
  {
    id: 'uv-prerelease-blocked',
    match: (raw) => prereleaseBlocked(raw),
    render: () => ({
      title: 'Pre-release dependency blocked by uv',
      explanation: 'We use a pre-release Python dependency, and uv refused to resolve it without explicit pre-release opt-in.',
      action: 'Re-run with `npx --yes claude-anyteam@latest`. If it still fails, run `uv tool install --prerelease=allow claude-anyteam`.',
      severity: HARD,
    }),
  },
  {
    id: 'uv-no-solution',
    match: (raw) => NO_SOLUTION.test(raw.text) && !prereleaseBlocked(raw),
    render: () => ({
      title: 'Python dependency conflict',
      explanation: 'uv could not find a compatible set of Python dependencies. Most often, another installed tool pinned an incompatible dependency version.',
      action: 'Run `uv tool install --reinstall claude-anyteam`, then re-run `npx --yes claude-anyteam@latest`.',
      severity: HARD,
    }),
  },
  {
    id: 'uv-windows-longpath',
    match: (raw) => isWindows(raw) && WINDOWS_LONG_PATH.test(raw.text),
    render: () => ({
      title: 'Windows long-path limit hit',
      explanation: 'Windows rejected a Python package path because it exceeded the legacy 260-character path limit.',
      action: "Open PowerShell as Admin and run `New-ItemProperty -Path 'HKLM:\\SYSTEM\\CurrentControlSet\\Control\\FileSystem' -Name 'LongPathsEnabled' -Value 1 -PropertyType DWORD -Force`, then reboot and re-run.",
      severity: HARD,
    }),
  },
  {
    id: 'uv-windows-store-python',
    match: (raw) => isWindows(raw) && WINDOWS_STORE_PYTHON.test(raw.text),
    render: () => ({
      title: 'Windows Store Python shim detected',
      explanation: "Windows pointed uv at the Microsoft Store Python shim instead of a real Python interpreter.",
      action: 'Install real Python from https://www.python.org/downloads/ and tick “Add to PATH”, or run `uv python install 3.12`.',
      severity: HARD,
    }),
  },
  {
    id: 'uv-python-missing',
    match: (raw) => PYTHON_MISSING.test(raw.text),
    render: () => ({
      title: 'Python 3.12+ not found',
      explanation: 'uv could not find a Python 3.12+ interpreter to install claude-anyteam.',
      action: 'Install Python 3.12+ from https://www.python.org/downloads/ (Windows: tick “Add to PATH”) or run `uv python install 3.12`.',
      severity: HARD,
    }),
  },
  {
    id: 'uv-network',
    match: (raw) => NETWORK.test(raw.text),
    render: () => ({
      title: 'Network problem reaching PyPI',
      explanation: 'uv could not reach the Python package index (PyPI), likely because of a proxy, VPN, DNS, TLS, or temporary network issue.',
      action: 'Set `HTTPS_PROXY` and `HTTP_PROXY` if needed, then re-run `npx --yes claude-anyteam@latest`; if you are on a VPN, try disconnecting briefly.',
      severity: HARD,
    }),
  },
  {
    id: 'uv-disk-full',
    match: (raw) => DISK_FULL.test(raw.text),
    render: () => ({
      title: 'Disk is full',
      explanation: 'uv could not finish installing because the filesystem ran out of free space.',
      action: 'Free up disk space (at least 200MB recommended), then re-run `npx --yes claude-anyteam@latest`.',
      severity: HARD,
    }),
  },
  {
    id: 'uv-permission-denied',
    match: (raw) => PERMISSION_DENIED.test(raw.text),
    render: () => ({
      title: 'Filesystem permission denied',
      explanation: 'uv could not write into an install/cache directory, likely because a directory under your home folder is owned by another user.',
      action: 'On macOS/Linux run `sudo chown -R $USER ~/.local`; on Windows, run the terminal as your normal (not Administrator) user.',
      severity: HARD,
    }),
  },
  {
    id: 'python-version-too-old',
    match: (raw) => isPythonVersionTooOld(raw.pythonVersion ?? extractDetectedPythonVersion(raw.text)),
    render: () => ({
      title: 'Python version is too old',
      explanation: 'Your detected Python is older than 3.12, but claude-anyteam needs Python 3.12 or newer.',
      action: 'Run `uv python install 3.12` or install Python 3.12+ from https://www.python.org/downloads/.',
      severity: HARD,
    }),
  },
  {
    id: 'claude-not-found',
    match: (raw) => raw.kind === 'claude-not-found' || CLAUDE_NOT_FOUND.test(raw.text),
    render: () => ({
      title: 'Claude Code CLI not detected',
      explanation: 'The installer skipped Claude Code plugin registration because `claude` was not available on PATH; the core spawn shim install can still be used.',
      action: 'Install Claude Code from https://docs.claude.com/en/docs/claude-code/setup and re-run, or proceed without the plugin by symlinking the spawn shim from your editor.',
      severity: SOFT,
    }),
  },
];

const fallbackPattern = {
  id: 'fallback-unrecognized',
  match: () => true,
  render: () => ({
    title: 'Unrecognized installer error',
    explanation: 'We hit an installer error we do not recognize yet, and we treat unrecognized installer errors as bugs.',
    action: `Copy-paste the raw details at ${ISSUE_URL}.`,
    severity: HARD,
  }),
};

export function translate(rawError, context = {}) {
  const raw = normalize(rawError, context);
  const pattern = patterns.find((candidate) => candidate.match(raw)) ?? fallbackPattern;
  const rendered = pattern.render(raw);
  return {
    id: pattern.id,
    title: rendered.title,
    explanation: rendered.explanation,
    action: rendered.action,
    severity: rendered.severity,
    raw: raw.text,
  };
}

export const translateInstallError = translate;
export default translate;

export const urls = {
  issues: ISSUE_URL,
  pythonDownloads: PYTHON_DOWNLOADS_URL,
  claudeCodeSetup: CLAUDE_CODE_SETUP_URL,
};
