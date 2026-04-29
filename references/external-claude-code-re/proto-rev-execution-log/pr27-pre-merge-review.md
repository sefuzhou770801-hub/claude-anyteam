# PR #27 pre-merge review — claude_native focused pass

Date: 2026-04-29  
Branch reviewed/fixed: `proto-rev/impl/pr27-pre-merge-cleanup`  
Scope: `src/claude_anyteam/backends/claude_native/*.py`, `src/claude_anyteam/capabilities.py`, `tests/test_claude_native_backend.py`, `docs/architecture.md`, `docs/adding-a-backend.md`, and `CHANGELOG.md`.

## Findings and actions

| Category | File:line | Severity | Recommendation | Status |
| --- | --- | --- | --- | --- |
| Capability declaration completeness | `src/claude_anyteam/backends/claude_native/loop.py:45-64`, `src/claude_anyteam/capabilities.py:84-90` | HIGH | fix-now-as-blocker | Fixed. The native-Claude backend was reusing the Kimi capability list, which advertised unsupported `session_resume`/`plan_mode`, omitted `live_tool_events`, and did not explicitly reflect Claude Code's native Task/Skill/WebFetch/Read/Edit/Write/Bash surface. Added `CLAUDE_NATIVE_HEADLESS_CAPABILITIES`, switched metadata to it, recorded the native host tool surface, and added tests. |
| Capability manifest accuracy | `src/claude_anyteam/capabilities.py:531-552` | HIGH | fix-now-as-blocker | Fixed. `native_skills` manifest prose/schema were Kimi-specific even when used by native Claude. Made the entry backend-neutral and added Claude Code discovery wording/tests. |
| Dead code / unused imports | `src/claude_anyteam/backends/claude_native/invoke.py:4-17`, `src/claude_anyteam/backends/claude_native/loop.py:8-24` | LOW | fix-now-as-cleanup | Fixed. Removed unused `datetime`/`timezone`, `_utc_now`, `PLAN_SCHEMA`, and `json` import; stopped exposing schema constants via `invoke` for native-Claude task execution. |
| Doc inconsistencies | `docs/architecture.md:6-162`, `CHANGELOG.md:9-25` | MEDIUM | fix-now-as-cleanup | Fixed. Architecture now documents the native-Claude path and truthful advertised capabilities; CHANGELOG now matches the capability vocabulary and avoids stale LOC/test-suite counts. |
| Test gaps | `tests/test_claude_native_backend.py:173-415`, `src/claude_anyteam/backends/claude_native/loop.py:178-409` | MEDIUM | nice-to-have-followup | Partially fixed for capability declarations. Remaining obvious gaps: no direct tests for `_execute_task` success/block/retry, `_handle_shutdown`, `_handle_prose` fallback vs delivered-via-tool paths, timeout/nonzero/schema-failure terminal visibility, or `feature_test` missing-flag failures. |
| API surface review | `src/claude_anyteam/wrapper_server.py:139-157`, `src/claude_anyteam/wrapper_server.py:1681-1758`, `src/claude_anyteam/wrapper_server.py:1810-1835` | MEDIUM | nice-to-have-followup | No claude_native-only peer-callable control tools found. Follow-up recommended: wrapper `read_config().protocol_tools` exposes unrestricted shadow tools (`mcp_anyteam_shell`, `mcp_anyteam_write_file`, `mcp_anyteam_edit_file`) to all routed backends; that may be intentional because backends already have native file/shell tools, but docs should not call these “non-destructive.” |
| Schema recovery strictness | `src/claude_anyteam/backends/claude_native/invoke.py:222-281` | LOW | nice-to-have-followup | `_embedded_json_object_candidates()` docstring says arbitrary trailing prose still fails, but `_parse_and_validate_final_message()` accepts any embedded valid object, including with trailing prose. Decide whether to tighten to preamble-only recovery or update the comment/test to match the permissive behavior. |
| Stale TODO/FIXME | `git grep -nE 'TODO|FIXME' -- src/claude_anyteam/backends/claude_native src/claude_anyteam/capabilities.py tests/test_claude_native_backend.py docs/architecture.md docs/adding-a-backend.md CHANGELOG.md` | LOW | no-action-needed | No TODO/FIXME entries found in the reviewed PR surfaces. |

## Verification

- Targeted: `/home/rosado/Projects/codex-teammate/.venv/bin/python -m pytest -q tests/test_claude_native_backend.py tests/test_capability_declarations.py tests/test_capability_validation.py` → 26 passed.
- Full suite: `/home/rosado/Projects/codex-teammate/.venv/bin/python -m pytest -q` → 1059 passed, 2 deselected, 1 warning.
