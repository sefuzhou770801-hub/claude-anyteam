from __future__ import annotations

import os
import shutil
import subprocess
import sys
import textwrap
from importlib import resources
from pathlib import Path

from claude_anyteam.schema_validation import load_schema, parse_and_validate


ROOT = Path(__file__).resolve().parents[1]
REQUIRED_SCHEMAS = (
    "task-complete.schema.json",
    "plan.schema.json",
    "permission_request.schema.json",
    "permission_response.schema.json",
    "capability_manifest.schema.json",
)


def test_schema_resources_resolve_via_importlib_resources() -> None:
    schemas_dir = resources.files("claude_anyteam.schemas")

    for name in REQUIRED_SCHEMAS:
        resource = schemas_dir.joinpath(name)
        assert resource.is_file(), name
        schema = load_schema(resource)
        assert schema.get("$schema") == "https://json-schema.org/draft/2020-12/schema"


def test_wheel_install_ships_schemas_and_validates_task_complete(tmp_path: Path) -> None:
    """R1/R2 (09 §3.1) acceptance: a fresh wheel install carries schemas.

    The install is isolated with ``pip --target`` and the subprocess runs from
    outside the repo with that target first on PYTHONPATH, so the import comes
    from the built wheel rather than the source tree. Each backend imports and
    validates the packaged task_complete schema resource.
    """
    if shutil.which("uv") is None:
        raise AssertionError("uv is required to build the wheel for this packaging test")

    dist_dir = tmp_path / "dist"
    target_dir = tmp_path / "site"
    subprocess.run(
        ["uv", "build", "--wheel", "--out-dir", str(dist_dir)],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    wheels = sorted(dist_dir.glob("claude_anyteam-*.whl"))
    assert wheels, "uv build did not produce a claude-anyteam wheel"

    subprocess.run(
        [
            "uv",
            "pip",
            "install",
            "--python",
            sys.executable,
            "--no-deps",
            "--target",
            str(target_dir),
            str(wheels[-1]),
        ],
        cwd=tmp_path,
        check=True,
        capture_output=True,
        text=True,
    )

    script = f"""
import importlib
import json
from importlib import resources
from pathlib import Path

import claude_anyteam
from claude_anyteam.schema_validation import load_schema, parse_and_validate

target = Path({str(target_dir)!r}).resolve()
loaded = Path(claude_anyteam.__file__).resolve()
assert target in loaded.parents, (target, loaded)

schemas = resources.files("claude_anyteam.schemas")
required = {REQUIRED_SCHEMAS!r}
missing = [name for name in required if not schemas.joinpath(name).is_file()]
assert not missing, missing

schema = load_schema(schemas.joinpath("task-complete.schema.json"))
parsed, err = parse_and_validate(
    '{{"files_changed": ["src/example.py"], "summary": "Validated from wheel."}}',
    schema,
)
assert err is None, err
assert parsed["summary"] == "Validated from wheel."

payload = '{{"files_changed": [], "summary": "Validated by backend import."}}'
backend_modules = {{
    "codex": "claude_anyteam.codex",
    "gemini_headless": "claude_anyteam.backends.gemini.invoke",
    "gemini_acp": "claude_anyteam.backends.gemini.acp",
    "kimi": "claude_anyteam.backends.kimi.invoke",
}}
for backend, module_name in backend_modules.items():
    module = importlib.import_module(module_name)
    backend_schema = load_schema(module.TASK_COMPLETE_SCHEMA)
    parsed, err = parse_and_validate(payload, backend_schema)
    assert err is None, (backend, err)
    assert parsed["summary"] == "Validated by backend import."
"""
    env = os.environ.copy()
    env["PYTHONPATH"] = str(target_dir)
    subprocess.run(
        [sys.executable, "-c", textwrap.dedent(script)],
        cwd=tmp_path,
        env=env,
        check=True,
        capture_output=True,
        text=True,
    )
