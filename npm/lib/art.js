import gradient from 'gradient-string';
import colors from 'yoctocolors';

export const banner = [
  ' ██████╗ ██████╗ ██████╗ ███████╗██╗  ██╗',
  '██╔════╝██╔═══██╗██╔══██╗██╔════╝╚██╗██╔╝',
  '██║     ██║   ██║██║  ██║█████╗   ╚███╔╝ ',
  '██║     ██║   ██║██║  ██║██╔══╝   ██╔██╗ ',
  '╚██████╗╚██████╔╝██████╔╝███████╗██╔╝ ██╗',
  ' ╚═════╝ ╚═════╝ ╚═════╝ ╚══════╝╚═╝  ╚═╝',
  '',
  '████████╗███████╗ █████╗ ███╗   ███╗███╗   ███╗ █████╗ ████████╗███████╗',
  '╚══██╔══╝██╔════╝██╔══██╗████╗ ████║████╗ ████║██╔══██╗╚══██╔══╝██╔════╝',
  '   ██║   █████╗  ███████║██╔████╔██║██╔████╔██║███████║   ██║   █████╗  ',
  '   ██║   ██╔══╝  ██╔══██║██║╚██╔╝██║██║╚██╔╝██║██╔══██║   ██║   ██╔══╝  ',
  '   ██║   ███████╗██║  ██║██║ ╚═╝ ██║██║ ╚═╝ ██║██║  ██║   ██║   ███████╗',
  '   ╚═╝   ╚══════╝╚═╝  ╚═╝╚═╝     ╚═╝╚═╝     ╚═╝╚═╝  ╚═╝   ╚═╝   ╚══════╝',
].join('\n');

const ansiPattern = /\u001B\[[0-?]*[ -/]*[@-~]/g;
const borderPalette = {
  cyan: colors.cyan,
  green: colors.green,
  red: colors.red,
  yellow: colors.yellow,
  magenta: colors.magenta,
  blue: colors.blue,
};
const headerGradient = gradient(['#22d3ee', '#60a5fa', '#a78bfa', '#f472b6']);
const shortBanner = 'claude-anyteam';

export const theme = {
  colors,
  accent: (value) => colors.bold(colors.cyan(value)),
  success: (value) => colors.bold(colors.green(value)),
  warn: (value) => colors.bold(colors.yellow(value)),
  danger: (value) => colors.bold(colors.red(value)),
  muted: (value) => colors.gray(value),
  heading: (value) => colors.bold(colors.white(value)),
  symbols: {
    success: colors.green('✔'),
    info: colors.cyan('●'),
    warn: colors.yellow('▲'),
    error: colors.red('✖'),
  },
};

export function renderBanner(options = {}) {
  const columns = Number(options.columns ?? process.env.COLUMNS ?? process.stdout?.columns ?? 80);
  if (Number.isFinite(columns) && columns > 0 && columns < 82) {
    return headerGradient(shortBanner);
  }
  return headerGradient.multiline(banner);
}

export function stripAnsi(value) {
  return String(value ?? '').replace(ansiPattern, '');
}

const BOX_MAX_WIDTH = 96;
const BOX_MIN_WIDTH = 40;

function wrapAnsiLine(rawRow, width) {
  if (stripAnsi(rawRow).length <= width) return [rawRow];
  const tokens = String(rawRow).split(/(\s+)/);
  const lines = [];
  let current = '';
  let currentVisible = 0;
  const flush = () => {
    if (current.length > 0) lines.push(current);
    current = '';
    currentVisible = 0;
  };
  for (const tok of tokens) {
    const tokVisible = stripAnsi(tok).length;
    if (tokVisible === 0) continue;
    if (tokVisible > width) {
      flush();
      let remaining = tok;
      while (stripAnsi(remaining).length > width) {
        let cut = 0, visible = 0;
        for (const ch of remaining) {
          if (ch === '') {
            const m = remaining.slice(cut).match(/^\[[0-?]*[ -/]*[@-~]/);
            if (m) { cut += m[0].length; continue; }
          }
          if (visible >= width) break;
          cut += ch.length;
          visible += 1;
        }
        lines.push(remaining.slice(0, cut));
        remaining = remaining.slice(cut);
      }
      if (stripAnsi(remaining).length > 0) {
        current = remaining;
        currentVisible = stripAnsi(remaining).length;
      }
      continue;
    }
    if (currentVisible + tokVisible > width) {
      flush();
      if (/^\s+$/.test(tok)) continue;
    }
    current += tok;
    currentVisible += tokVisible;
  }
  flush();
  return lines.length > 0 ? lines : [rawRow];
}

export function renderBox(title, lines, color = 'cyan') {
  const paint = borderPalette[color] ?? ((value) => value);
  const termCols = Number(process.env.COLUMNS ?? process.stdout?.columns ?? 80);
  const budget = Math.max(BOX_MIN_WIDTH, Math.min(BOX_MAX_WIDTH, (Number.isFinite(termCols) ? termCols : 80) - 4));
  const bodyRows = (Array.isArray(lines) ? lines : [lines])
    .flatMap((row) => String(row).split('\n'))
    .flatMap((row) => wrapAnsiLine(row, budget));
  const titleRows = wrapAnsiLine(String(title), budget);
  const rows = [...titleRows, ...bodyRows];
  const width = Math.max(...rows.map((row) => stripAnsi(row).length), 0);
  const fill = (row) => `${row}${' '.repeat(width - stripAnsi(row).length)}`;
  const headerLines = titleRows.map((row) => `${paint('│')} ${fill(row)} ${paint('│')}`);
  const bodyLines = bodyRows.map((row) => `${paint('│')} ${fill(row)} ${paint('│')}`);
  return [
    paint(`╭${'─'.repeat(width + 2)}╮`),
    ...headerLines,
    paint(`├${'─'.repeat(width + 2)}┤`),
    ...bodyLines,
    paint(`╰${'─'.repeat(width + 2)}╯`),
  ].join('\n');
}
