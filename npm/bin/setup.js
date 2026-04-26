#!/usr/bin/env node

import { spawn } from 'node:child_process';
import readline from 'node:readline/promises';
import process from 'node:process';
import yoctoSpinner from 'yocto-spinner';
import {
  TOOL_NAME,
  UV_INSTALL_DIR,
  UV_TOOL_BIN_DIR,
  detectPython,
  detectUv,
  ensureWindowsLongPaths,
  findInstalledTool,
  formatDisplayPath,
  formatCommand,
  installTool,
  installUv,
  isCI,
  isInteractive,
  manualInstallLines,
  providerPrereqLines,
  runCommand,
  uvToolEnv,
  windowsInstallAdvice,
  which,
} from '../lib/detect.js';
import { renderBanner, renderBox, theme } from '../lib/art.js';
import { isPythonVersionTooOld, translate } from '../lib/error-translator.js';

const CLAUDE_PLUGIN_MARKETPLACE_SOURCE = 'JonathanRosado/claude-anyteam';
const CLAUDE_PLUGIN_MARKETPLACE_NAME = 'claude-anyteam';
const CLAUDE_PLUGIN_SPEC = `${CLAUDE_PLUGIN_MARKETPLACE_NAME}@${CLAUDE_PLUGIN_MARKETPLACE_NAME}`;
const CLAUDE_PLUGIN_MANUAL_COMMANDS = [
  formatCommand('claude', ['plugin', 'marketplace', 'add', CLAUDE_PLUGIN_MARKETPLACE_SOURCE]),
  formatCommand('claude', ['plugin', 'install', CLAUDE_PLUGIN_SPEC]),
  // Idempotent when already up-to-date; pulls the latest manifest otherwise.
  // Running it on every install is how re-runs of the installer pick up new
  // skill content without forcing the user to uninstall by hand.
  formatCommand('claude', ['plugin', 'update', CLAUDE_PLUGIN_SPEC]),
];
const CLAUDE_PLUGIN_MARKETPLACE_ALREADY_EXISTS = /\balready (?:on disk|exists)\b/i;
const CLAUDE_PLUGIN_ALREADY_INSTALLED = /\balready installed\b/i;

function parseArgs(argv) {
  const args = { postinstall: false, settingsPath: undefined, help: false };
  for (let index = 0; index < argv.length; index += 1) {
    const arg = argv[index];
    if (arg === '--postinstall') {
      args.postinstall = true;
    } else if (arg === '--settings-path') {
      if (index + 1 >= argv.length) {
        throw new Error('--settings-path requires a value');
      }
      args.settingsPath = argv[index + 1];
      index += 1;
    } else if (arg === '--help' || arg === '-h') {
      args.help = true;
    } else {
      throw new Error(`Unknown option: ${arg}`);
    }
  }
  return args;
}

function usage() {
  const settingsPath = process.platform === 'win32' ? '~\\.claude\\settings.json' : '~/.claude/settings.json';
  const claudeJsonPath = process.platform === 'win32' ? '~\\.claude.json' : '~/.claude.json';
  const prereqSummary = process.platform === 'win32'
    ? 'uses Windows single-terminal mode when tmux/psmux is unavailable, probes for the Codex CLI 0.120+, Gemini CLI, and Kimi CLI'
    : 'verifies tmux/psmux on PATH and probes for the Codex CLI 0.120+, Gemini CLI, and Kimi CLI';
  return [
    'Usage: npx --yes claude-anyteam [--settings-path <path>] [--postinstall]',
    '',
    'Installs uv if needed, installs the Python claude-anyteam tool, delegates',
    `the ${settingsPath} + ${claudeJsonPath} writes to the Python installer`,
    `(which ${prereqSummary}),`,
    'and registers the Claude Code plugin when the claude CLI is available on PATH.',
  ].join('\n');
}

async function confirmInstallUv() {
  const prompt = `${theme.symbols.info} ${theme.heading('uv is missing.')} Install it now into ${theme.accent(UV_INSTALL_DIR)}? ${theme.muted('[Y/n] ')}`;
  const rl = readline.createInterface({ input: process.stdin, output: process.stdout });
  try {
    const answer = (await rl.question(prompt)).trim().toLowerCase();
    return answer === '' || answer === 'y' || answer === 'yes';
  } finally {
    rl.close();
  }
}

async function withSpinner(text, enabled, action) {
  if (!enabled) {
    return action();
  }
  const spinner = yoctoSpinner({ text, color: 'cyan' }).start();
  try {
    const result = await action();
    spinner.success(`${theme.success('done')} ${theme.muted(text)}`);
    return result;
  } catch (error) {
    spinner.error(`${theme.danger('failed')} ${theme.muted(text)}`);
    throw error;
  }
}

function printFailure(title, lines) {
  console.error('');
  console.error(renderBox(theme.danger(title), lines, 'red'));
  console.error('');
}

function printSuccess(lines) {
  console.log('');
  console.log(renderBox(theme.success('INSTALL COMPLETE'), lines, 'green'));
  console.log('');
}

function printWarning(title, lines) {
  console.log('');
  console.log(renderBox(theme.warn(title), lines, 'yellow'));
  console.log('');
}

function rawDetailsLines(raw) {
  const text = String(raw || '');
  if (!text) {
    return ['    No extra diagnostics.'];
  }
  return text.split(/\r?\n/).map((line) => `    ${line}`);
}

function translatedDiagnosticLines(translated, { command, recovered } = {}) {
  const statusSymbol = translated.severity === 'soft' ? theme.symbols.success : theme.symbols.error;
  const lines = [
    `${statusSymbol} ${theme.heading(translated.title)}`,
    `${theme.symbols.info} ${theme.heading('What happened')}: ${translated.explanation}`,
  ];
  if (recovered) {
    lines.push(`${theme.symbols.success} ${theme.heading('Recovered')}: ${recovered}`);
  }
  lines.push(`${theme.symbols.info} ${theme.heading('Next step')}: ${translated.action}`);
  if (command) {
    lines.push(`${theme.symbols.info} ${theme.heading('Command')}: ${theme.accent(command)}`);
  }
  lines.push(`${theme.symbols.info} ${theme.heading('Raw details')}:`);
  lines.push(...rawDetailsLines(translated.raw));
  return lines;
}

function trimmedDetails(error) {
  return String(error?.details || error?.message || '').trim();
}

function pythonLabel() {
  return process.platform === 'win32' ? 'Python 3' : 'python3';
}

function settingsDisplayPath() {
  return process.platform === 'win32' ? '~\\.claude\\settings.json' : '~/.claude/settings.json';
}

function claudeJsonDisplayPath() {
  return process.platform === 'win32' ? '~\\.claude.json' : '~/.claude.json';
}

function workspaceExamplePath() {
  return process.platform === 'win32' ? 'C:\\path\\to\\workspace' : '/path/to/workspace';
}

function powershellLongPathsCommand() {
  return 'New-ItemProperty -Path "HKLM:\\SYSTEM\\CurrentControlSet\\Control\\FileSystem" -Name LongPathsEnabled -Value 1 -PropertyType DWord -Force';
}

function pythonMissingTranslation(issue) {
  if (issue?.kind === 'windows-store-python-stub') {
    return {
      id: 'uv-windows-store-python',
      title: 'Windows Store Python shim detected',
      explanation: 'Windows resolved python.exe to the Microsoft Store app execution alias, not a working Python interpreter.',
      action: 'From PowerShell run `winget install Python.Python.3.12`; if Windows still opens the Store, disable python.exe and python3.exe under Settings > Apps > Advanced app settings > App execution aliases.',
      severity: 'hard',
      raw: `Microsoft Store Python app execution alias at ${formatDisplayPath(issue.path)}: ${issue.details}`,
    };
  }
  return translate('no python interpreter found');
}

function windowsAdvice(error, options = {}) {
  return windowsInstallAdvice(error, options);
}

function windowsAdviceLines(advice) {
  return advice.map((line) => `${theme.symbols.info} ${line}`);
}

function postinstallHint(error) {
  const reason = trimmedDetails(error) || error.message;
  console.warn(`claude-anyteam: automatic setup skipped (${reason.split(/\r?\n/, 1)[0]}). Run npx --yes claude-anyteam to finish.`);
}

function claudePluginManualSummary() {
  return `skipped (install manually: ${CLAUDE_PLUGIN_MANUAL_COMMANDS.join(' && ')})`;
}

function claudePluginManualLines() {
  return CLAUDE_PLUGIN_MANUAL_COMMANDS.map((command) => `    ${theme.accent(command)}`);
}

function claudeNotFoundTranslation() {
  return translate({
    kind: 'claude-not-found',
    raw: 'claude CLI not detected on PATH',
  });
}

function skippedClaudePluginResult(reasonLines) {
  return {
    status: 'skipped',
    summary: claudePluginManualSummary(),
    warningTitle: 'CLAUDE CODE PLUGIN SKIPPED',
    warningLines: reasonLines,
  };
}

function failedClaudePluginResult(error) {
  return skippedClaudePluginResult([
    `${theme.symbols.warn} ${theme.heading('Claude settings were written, but the Claude Code plugin could not be registered automatically.')}`,
    `${theme.symbols.info} Details: ${trimmedDetails(error) || 'No extra diagnostics.'}`,
    `${theme.symbols.info} Run these commands manually:`,
    '',
    ...claudePluginManualLines(),
  ]);
}

// Delegate the three-file install (~/.claude/settings.json, ~/.claude.json,
// install-state.json + plugin-data dir) to the Python installer. The Python
// installer is the single source of truth for prereq checks, display-mode
// install instructions, teammateMode handling, and state-file writes.
//
// Primary invocation is `uv tool run --from claude-anyteam claude-anyteam
// install --assume-yes`: it resolves the tool's venv without depending on
// the user's shell PATH being refreshed post-`uv tool install`.
//
// stdio is fully inherited so the Python installer's platform-aware
// instructions reach the user verbatim. We do not re-wrap Python's errors; its messages
// are more actionable than any JS prose we could layer on top.
//
// The caller is expected to pass --settings-path only when explicitly
// overridden by a user; the Python installer already defaults to
// ~/.claude/settings.json.
function runPythonInstaller({ uvPath, settingsPath, stdio = 'inherit' }) {
  const args = [
    '--no-config',
    'tool',
    'run',
    '--from', TOOL_NAME,
    TOOL_NAME,
    'install',
    '--assume-yes',
  ];
  if (settingsPath) {
    args.push('--settings-path', settingsPath);
  }
  return new Promise((resolve, reject) => {
    const child = spawn(uvPath, args, {
      env: uvToolEnv(process.env),
      stdio,
    });
    child.on('error', reject);
    child.on('exit', (code, signal) => {
      if (signal) {
        reject(new Error(`Python installer terminated by signal ${signal}`));
        return;
      }
      resolve({ code: code ?? 1 });
    });
  });
}

async function runClaudePluginCommand(claudePath, args, alreadyPattern) {
  const result = await runCommand(claudePath, args, { env: process.env });
  const combined = `${result.stdout}\n${result.stderr}`;
  if (result.code !== 0 && !alreadyPattern.test(combined)) {
    const error = new Error(`Command failed: ${formatCommand('claude', args)}`);
    error.details = combined.trim();
    error.command = formatCommand('claude', args);
    throw error;
  }
  return { verified: alreadyPattern.test(combined) };
}

// Run `install` then `update` on every invocation so a re-run of
// `npx --yes claude-anyteam` picks up the latest plugin manifest (and its
// cached skills). `install` is a no-op when the plugin is already present;
// `update` is idempotent when already on the latest version and pulls the
// newer manifest otherwise. Together they cover fresh and upgrade paths
// without a destructive uninstall step.
async function registerClaudePlugin({ claudePath }) {
  await runClaudePluginCommand(
    claudePath,
    ['plugin', 'marketplace', 'add', CLAUDE_PLUGIN_MARKETPLACE_SOURCE],
    CLAUDE_PLUGIN_MARKETPLACE_ALREADY_EXISTS,
  );
  await runClaudePluginCommand(
    claudePath,
    ['plugin', 'install', CLAUDE_PLUGIN_SPEC],
    CLAUDE_PLUGIN_ALREADY_INSTALLED,
  );
  await runClaudePluginCommand(
    claudePath,
    ['plugin', 'update', CLAUDE_PLUGIN_SPEC],
    // Exits 0 on "already at the latest version" — no tolerance pattern
    // needed. Any non-zero here is a genuine update failure.
    /^$/,
  );
  return { status: 'refreshed', summary: 'installed + checked for updates' };
}

async function main() {
  const args = parseArgs(process.argv.slice(2));
  if (args.help) {
    console.log(usage());
    return 0;
  }

  const postinstall = args.postinstall || process.env.npm_lifecycle_event === 'postinstall';
  const interactive = isInteractive();
  const silent = postinstall;

  if (!silent) {
    console.log(renderBanner());
    console.log(theme.heading('Zero-friction claude-anyteam setup for Claude Code.'));
    console.log(theme.muted('We will check Python, install uv if needed, wire up claude-anyteam, patch Claude settings, and register the Claude plugin.'));
    console.log('');
  }

  const pythonCheck = await detectPython({ diagnostics: true });
  const python = pythonCheck.python;
  if (!python) {
    if (silent) {
      postinstallHint(new Error(`${pythonLabel()} was not found`));
      return 0;
    }
    const translated = pythonMissingTranslation(pythonCheck.issue);
    printFailure('PYTHON 3.12+ NOT FOUND', [
      ...translatedDiagnosticLines(translated),
      '',
      ...manualInstallLines({ includePython: true }).map((line) => `${theme.symbols.info} ${line}`),
    ]);
    return 1;
  }

  if (!silent) {
    console.log(`${theme.symbols.success} ${theme.heading(`${pythonLabel()} detected`)} ${theme.muted(`(${python.version})`)} ${theme.accent(formatDisplayPath(python.path))}`);
  }

  if (isPythonVersionTooOld(python.version)) {
    if (silent) {
      postinstallHint(new Error(`Python ${python.version} is too old; Python 3.12+ is required`));
      return 0;
    }
    const translated = translate({
      raw: `Detected Python ${python.version} at ${formatDisplayPath(python.path)}`,
      pythonVersion: python.version,
    });
    printFailure('PYTHON VERSION TOO OLD', translatedDiagnosticLines(translated));
    return 1;
  }

  let uv = await detectUv();
  if (!uv) {
    if (process.platform === 'win32') {
      if (silent) {
        postinstallHint(new Error('uv is not installed'));
        return 0;
      }
      printFailure('UV NOT INSTALLED', [
        `${theme.symbols.warn} ${theme.heading('uv is required to install the Python claude-anyteam tool.')}`,
        `${theme.symbols.info} Install it from PowerShell, then rerun ${theme.accent('npx --yes claude-anyteam')}.`,
        '',
        ...manualInstallLines().map((line) => `${theme.symbols.info} ${line}`),
      ]);
      return 1;
    }
    const autoInstall = postinstall || !interactive || (await confirmInstallUv());
    if (!autoInstall) {
      if (silent) {
        postinstallHint(new Error('uv is not installed'));
        return 0;
      }
      printFailure('UV NOT INSTALLED', [
        `${theme.symbols.warn} ${theme.heading('uv is required to install the Python claude-anyteam tool.')}`,
        `${theme.symbols.info} Install it manually, then rerun ${theme.accent('npx --yes claude-anyteam')}.`,
        '',
        ...manualInstallLines().map((line) => `${theme.symbols.info} ${line}`),
      ]);
      return 1;
    }

    try {
      uv = await withSpinner(`Installing uv into ${UV_INSTALL_DIR}`, !silent, () => installUv());
    } catch (error) {
      if (silent) {
        postinstallHint(error);
        return 0;
      }
      printFailure('UV INSTALL FAILED', [
        `${theme.symbols.error} ${theme.heading('Automatic uv installation did not complete.')}`,
        `${theme.symbols.info} Installer output: ${trimmedDetails(error) || 'No extra diagnostics.'}`,
        '',
        ...manualInstallLines().map((line) => `${theme.symbols.info} ${line}`),
      ]);
      return 1;
    }
  }

  if (!silent) {
    console.log(`${theme.symbols.success} ${theme.heading('uv ready')} ${theme.muted(uv.version)} ${theme.accent(formatDisplayPath(uv.path))}`);
  }

  if (process.platform === 'win32') {
    const longPaths = await ensureWindowsLongPaths();
    if (!silent && longPaths.changed) {
      printWarning('WINDOWS LONG PATHS ENABLED', [
        `${theme.symbols.success} ${theme.heading('Enabled LongPathsEnabled in the Windows registry for uv tool installs.')}`,
        `${theme.symbols.info} If uv still reports path-too-long errors, close and reopen PowerShell or reboot Windows, then rerun ${theme.accent('npx --yes claude-anyteam')}.`,
      ]);
    } else if (!silent && longPaths.supported && !longPaths.enabled) {
      printWarning('WINDOWS LONG PATHS MAY BE DISABLED', [
        `${theme.symbols.warn} ${theme.heading('uv tool install can exceed the default 260-character Windows path limit.')}`,
        `${theme.symbols.info} Automatic registry update did not succeed: ${trimmedDetails(longPaths) || 'No extra diagnostics.'}`,
        `${theme.symbols.info} Next step: open PowerShell as Administrator and run:`,
        `    ${theme.accent(powershellLongPathsCommand())}`,
      ]);
    }
  }

  let tool;
  const existingTool = await findInstalledTool({ uvPath: uv.path }).catch(() => null);
  if (existingTool) {
    tool = existingTool;
    if (!silent) {
      console.log(`${theme.symbols.success} ${theme.heading('existing claude-anyteam tool detected')} ${theme.accent(formatDisplayPath(tool.binaryPath))}`);
    }
  } else {
    try {
      tool = await withSpinner(`Installing ${TOOL_NAME} with uv tool install`, !silent, () => installTool({ uvPath: uv.path, pythonPath: python.path }));
    } catch (error) {
      if (silent) {
        postinstallHint(error);
        return 0;
      }
      const command = error.command || formatCommand(uv.path, ['--no-config', 'tool', 'install', '--force', '--prerelease=allow', '--python', python.path, TOOL_NAME]);
      const translated = translate(error.details || error.message, {
        platform: process.platform,
        pythonVersion: python.version,
      });
      const advice = windowsAdvice(error, { binDir: UV_TOOL_BIN_DIR });
      const translatedForDisplay = process.platform === 'win32' && translated.id === 'uv-permission-denied' && advice.length
        ? { ...translated, action: advice.join(' ') }
        : translated;
      printFailure('TOOL INSTALL FAILED', [
        ...translatedDiagnosticLines(translatedForDisplay, { command }),
        ...(advice.length ? ['', ...windowsAdviceLines(advice)] : []),
      ]);
      return 1;
    }
  }

  // Delegate Claude settings + claude.json + state file to the Python installer.
  // We do NOT wrap this in withSpinner — the Python installer writes its own
  // status lines to stdout, and overlaying a spinner would fight with that output.
  if (!silent) {
    console.log('');
    const pythonInstallerSummary = process.platform === 'win32'
      ? `— Windows single-terminal compatibility, Codex/Gemini/Kimi CLI prereq checks, ${settingsDisplayPath()}, ${claudeJsonDisplayPath()}, install-state.json`
      : `— tmux + Codex/Gemini/Kimi CLI prereq checks, ${settingsDisplayPath()}, ${claudeJsonDisplayPath()}, install-state.json`;
    console.log(`${theme.symbols.info} ${theme.heading('Running claude-anyteam install (Python)')} ${theme.muted(pythonInstallerSummary)}`);
  }
  let installerResult;
  try {
    installerResult = await runPythonInstaller({
      uvPath: uv.path,
      settingsPath: args.settingsPath,
      stdio: silent ? 'ignore' : 'inherit',
    });
  } catch (error) {
    if (silent) {
      postinstallHint(error);
      return 0;
    }
    // Only fires when the Python installer could not be spawned at all
    // (e.g. uv binary vanished mid-run). Real install errors arrive via
    // non-zero exit code with inherited stderr already printed to the user.
    printFailure('UNABLE TO RUN PYTHON INSTALLER', [
      `${theme.symbols.error} ${theme.heading('Could not invoke the Python installer through uv.')}`,
      `${theme.symbols.info} Details: ${trimmedDetails(error) || error.message}`,
      `${theme.symbols.info} Retry with ${theme.accent(formatCommand(tool.binaryPath, ['install', '--assume-yes']))} after ensuring uv is on PATH.`,
      ...windowsAdviceLines(windowsAdvice(error, { binDir: tool.binDir })),
    ]);
    return 1;
  }
  if (installerResult.code !== 0) {
    if (silent) {
      postinstallHint(new Error(`Python installer exited with code ${installerResult.code}`));
      return 0;
    }
    // Python installer already streamed its own actionable message (tmux
    // install hints, teammateMode conflict explanation, etc.) through
    // inherited stderr. Do not re-wrap; exit non-zero quietly.
    if (installerResult.code === 3) {
      console.error(`${theme.symbols.warn} Python installer aborted (exit code 3) despite --assume-yes. This is unexpected — please file a bug at https://github.com/JonathanRosado/claude-anyteam/issues`);
    }
    return 1;
  }

  let claudePlugin = null;
  const claudePath = await which('claude');
  if (!claudePath) {
    const translated = claudeNotFoundTranslation();
    claudePlugin = skippedClaudePluginResult([
      ...translatedDiagnosticLines(translated, {
        recovered: 'Skipped Claude Code plugin registration and continued with the core claude-anyteam install.',
      }),
      '',
      `${theme.symbols.info} Run these commands once ${theme.accent('claude')} is available:`,
      '',
      ...claudePluginManualLines(),
    ]);
    claudePlugin.warningTitle = 'CLAUDE CODE PLUGIN RECOVERED';
    if (!silent) {
      printWarning(claudePlugin.warningTitle, claudePlugin.warningLines);
    }
  } else {
    try {
      claudePlugin = await withSpinner(`Registering Claude Code plugin`, !silent, () => registerClaudePlugin({ claudePath }));
    } catch (error) {
      claudePlugin = failedClaudePluginResult(error);
      if (!silent) {
        printWarning(claudePlugin.warningTitle, claudePlugin.warningLines);
      }
    }
  }

  if (silent) {
    return 0;
  }

  // The Python installer already printed the env-var and teammateMode changes
  // to stdout. Success box avoids duplicating that — it covers only what Python
  // doesn't know about (plugin registration status, launch template, restart
  // reminder) so there's no noise.
  const launchTemplate = `${formatCommand(tool.binaryPath, ['--team', 'my-team', '--name', 'codex-alice', '--cwd', workspaceExamplePath()])}  # or --name gemini-alice / kimi-architect`;
  const toolVerb = tool.installMode === 'existing' ? 'reused existing install' : 'installed with uv tool install';
  const providerLines = providerPrereqLines().map((line) => `${theme.symbols.info} ${line}`);
  printSuccess([
    `${theme.symbols.success} Tool status = ${theme.accent(toolVerb)}`,
    `${theme.symbols.info} Claude Code plugin: ${theme.accent(claudePlugin.summary)}`,
    `${theme.symbols.info} uv tool bin directory = ${theme.accent(formatDisplayPath(tool.binDir))}`,
    '',
    `${theme.symbols.info} Launch template:`,
    `    ${theme.accent(launchTemplate)}`,
    '',
    ...providerLines,
    '',
    `${theme.symbols.warn} Restart Claude Code so it reloads ${theme.accent(settingsDisplayPath())}.`,
  ]);
  console.log(`${theme.symbols.success} ${theme.heading('Your claude-anyteam launcher is live.')} Codex/Gemini/Kimi-powered teammates use the ${theme.accent('codex-')}, ${theme.accent('gemini-')}, or ${theme.accent('kimi-')} prefix today in Claude Code's external spawn flow.`);
  return 0;
}

main().then(
  (code) => {
    process.exitCode = code;
  },
  (error) => {
    if (process.argv.includes('--postinstall') || process.env.npm_lifecycle_event === 'postinstall' || isCI()) {
      postinstallHint(error);
      process.exitCode = 0;
      return;
    }
    printFailure('UNEXPECTED INSTALLER ERROR', [
      `${theme.symbols.error} ${theme.heading(error.message)}`,
      ...(trimmedDetails(error) && trimmedDetails(error) !== error.message ? [`${theme.symbols.info} ${trimmedDetails(error)}`] : []),
    ]);
    process.exitCode = 1;
  },
);
