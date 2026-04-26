# Installer error fixtures

Fixture files in this directory are raw stderr captures for installer hardening.
Each `.txt` filename is the canonical pattern id. The first `#` line records
whether the stderr is captured or synthesized; tests should strip leading `#`
metadata before handing the remaining text to an error translator.

| Fixture | Source | Scenario | Assertions the translator must support |
| --- | --- | --- | --- |
| `uv-prerelease-blocked.txt` | Captured from user-reported `uv tool install` failure | uv's PubGrub resolver refuses `fastmcp==3.0.0b1` because pre-releases were not enabled. | Pattern id `uv-prerelease-blocked`; non-empty `title`, `explanation`, `action`, `severity`; `action` mentions `--prerelease=allow`. |
| `uv-no-solution-conflict.txt` | Synthesized from uv/PubGrub resolver wording | Two requested packages have incompatible `fastmcp` constraints. | Pattern id `uv-no-solution-conflict`; actionable dependency-conflict guidance; non-fallback severity. |
| `uv-network-timeout.txt` | Synthesized from uv download/retry wording | PyPI wheel download times out after retries. | Pattern id `uv-network-timeout`; action should suggest retrying/checking network or proxy/VPN configuration. |
| `uv-windows-longpath.txt` | Synthesized from uv Windows wheel copy failure | Windows install fails with `os error 206` because long paths are disabled. | Pattern id `uv-windows-longpath`; `action` mentions `LongPathsEnabled`. |
| `python-store-stub.txt` | Captured Windows Python app-execution-alias stderr | The `python` command resolves to the Microsoft Store stub instead of a real interpreter. | Pattern id `python-store-stub`; action should tell users to install Python or disable App execution aliases. |
| `settings-json-corrupt.txt` | Synthesized from Python `json.decoder.JSONDecodeError` + installer wrapper | `~/.claude/settings.json` is malformed before install. | Pattern id `settings-json-corrupt`; action should point at fixing/backing up `settings.json`. |
| `claude-cli-not-found.txt` | Synthesized from shell/Node command failure | Claude Code CLI is missing from `PATH` during plugin registration. | Pattern id `claude-cli-not-found`; action should tell users to install/open Claude Code CLI or rerun plugin commands after `claude` is available. |
| `claude-plugin-marketplace-failed.txt` | Synthesized from `claude plugin marketplace add` failure | Plugin marketplace registration cannot fetch the GitHub-hosted manifest. | Pattern id `claude-plugin-marketplace-failed`; action should suggest retrying auth/network and manual plugin commands. |
| `uv-python-not-found.txt` | Synthesized from uv interpreter-resolution wording | uv cannot find the requested Python version. | Pattern id `uv-python-not-found`; action should suggest installing the requested Python or allowing uv-managed Python downloads. |
| `tmux-not-found.txt` | Synthesized from claude-anyteam installer prereq failure | No supported terminal multiplexer (`tmux`/`psmux`) is on `PATH`. | Pattern id `tmux-not-found`; action should include platform install commands for `tmux`/`psmux`. |
| `permission-denied-settings.txt` | Synthesized from Python `PermissionError` + installer wrapper | Installer cannot replace `~/.claude/settings.json`. | Pattern id `permission-denied-settings`; action should address file ownership/permissions without recommending blind `sudo` on the installer. |
| `uv-cache-permission-denied.txt` | Synthesized from uv cache directory failure | uv cannot create files under its cache directory. | Pattern id `uv-cache-permission-denied`; action should tell users to fix cache ownership/permissions or clear the uv cache. |

Fallback coverage belongs in translator tests: an unrecognized stderr should
return a soft fallback, include the raw output, and link to
<https://github.com/JonathanRosado/claude-anyteam/issues>.
