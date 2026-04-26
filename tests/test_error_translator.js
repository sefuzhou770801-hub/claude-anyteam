import assert from 'node:assert/strict';
import { test } from 'node:test';
import { translate } from '../npm/lib/error-translator.js';

function assertTranslation(raw, context, expected) {
  const result = translate(raw, context);
  assert.equal(result.id, expected.id);
  assert.equal(result.title, expected.title);
  assert.equal(result.severity, expected.severity);
  assert.match(result.action, expected.action);
}

test('uv-prerelease-blocked', () => {
  assertTranslation(
    "Because pre-release versions weren't enabled, try --prerelease=allow",
    {},
    {
      id: 'uv-prerelease-blocked',
      title: 'Pre-release dependency blocked by uv',
      severity: 'hard',
      action: /uv tool install --prerelease=allow claude-anyteam/,
    },
  );
});

test('uv-no-solution', () => {
  assertTranslation(
    'error: no solution found when resolving dependencies for claude-anyteam',
    {},
    {
      id: 'uv-no-solution',
      title: 'Python dependency conflict',
      severity: 'hard',
      action: /uv tool install --reinstall claude-anyteam/,
    },
  );
});

test('uv-python-missing', () => {
  assertTranslation(
    'error: could not find an interpreter for Python 3.12',
    {},
    {
      id: 'uv-python-missing',
      title: 'Python 3.12+ not found',
      severity: 'hard',
      action: /uv python install 3\.12/,
    },
  );
});

test('uv-network', () => {
  assertTranslation(
    'error: connection timed out while fetching https://pypi.org/simple/claude-anyteam',
    {},
    {
      id: 'uv-network',
      title: 'Network problem reaching PyPI',
      severity: 'hard',
      action: /HTTPS_PROXY/,
    },
  );
});

test('uv-disk-full', () => {
  assertTranslation(
    'OSError: [Errno 28] No space left on device',
    {},
    {
      id: 'uv-disk-full',
      title: 'Disk is full',
      severity: 'hard',
      action: /200MB/,
    },
  );
});

test('uv-permission-denied', () => {
  assertTranslation(
    'Permission denied (os error 13) while writing ~/.local/bin',
    {},
    {
      id: 'uv-permission-denied',
      title: 'Filesystem permission denied',
      severity: 'hard',
      action: /sudo chown -R \$USER ~\/\.local/,
    },
  );
});

test('uv-windows-longpath', () => {
  assertTranslation(
    'The specified path exceeds the maximum supported path length',
    { platform: 'win32' },
    {
      id: 'uv-windows-longpath',
      title: 'Windows long-path limit hit',
      severity: 'hard',
      action: /LongPathsEnabled/,
    },
  );
});

test('uv-windows-store-python', () => {
  assertTranslation(
    'python.exe not found. Install Python from the Microsoft Store?',
    { platform: 'win32' },
    {
      id: 'uv-windows-store-python',
      title: 'Windows Store Python shim detected',
      severity: 'hard',
      action: /uv python install 3\.12/,
    },
  );
});

test('python-version-too-old', () => {
  assertTranslation(
    'Detected Python 3.11.8 at /usr/bin/python3',
    { pythonVersion: '3.11.8' },
    {
      id: 'python-version-too-old',
      title: 'Python version is too old',
      severity: 'hard',
      action: /Python 3\.12\+/,
    },
  );
});

test('claude-not-found', () => {
  assertTranslation(
    'Claude Code CLI not detected on PATH',
    {},
    {
      id: 'claude-not-found',
      title: 'Claude Code CLI not detected',
      severity: 'soft',
      action: /claude-code\/setup/,
    },
  );
});

test('fallback-unrecognized', () => {
  const raw = 'mystery installer failure\nwith untouched raw output';
  const result = translate(raw);

  assert.equal(result.id, 'fallback-unrecognized');
  assert.equal(result.title, 'Unrecognized installer error');
  assert.equal(result.severity, 'hard');
  assert.match(result.action, /issues\/new/);
  assert.equal(result.raw, raw);
});
