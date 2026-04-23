# Vendored: cs50victor/claude-code-teams-mcp

This directory preserves source, tests, and docs from the upstream
`claude-teams` library so we can extend the team protocol for new LLM
backends without losing the upstream context.

**Upstream:** https://github.com/cs50victor/claude-code-teams-mcp
**Upstream commit at vendoring time:** `bde20889622745bb0afd293060104105b85dfad2` (2026-02-21T05:21:28-08:00)
**License:** MIT (see `LICENSE`)
**Original package name:** `claude-teams`
**Vendored as:** `src/claude_teams/` (kept the module name so existing imports
                 like `from claude_teams._filelock import file_lock` keep working)

## Contents

- `LICENSE` — upstream MIT license, preserved
- `UPSTREAM-README.md` — original project README
- `UPSTREAM-pyproject.toml` — original package metadata, useful when comparing
  against future upstream releases
- `upstream-tests/` — original test suite, kept for protocol-compliance
  verification when we extend the protocol
- `stress_test_lifecycle.py` — original lifecycle stress test

## Maintenance notes

- Module code lives at `src/claude_teams/` (one level up, not in this folder).
- When you change protocol semantics for a new backend (Gemini, Kimi, etc.),
  update both the module and any affected tests in `upstream-tests/`.
- To compare against newer upstream, run:
  `git diff bde20889622745bb0afd293060104105b85dfad2 HEAD -- src/claude_teams/` against a fresh upstream
  clone.
