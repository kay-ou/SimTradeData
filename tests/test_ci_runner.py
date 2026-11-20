"""Exercise the npm CI entrypoint with isolated fake uv commands."""

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.skipif(
    os.name == "nt" or shutil.which("npm") is None,
    reason="Fake uv executable requires POSIX and npm",
)


@pytest.fixture
def ci_project(tmp_path):
    root = Path(__file__).resolve().parents[1]
    project = tmp_path / "project"
    scripts = project / "scripts" / "cicd"
    scripts.mkdir(parents=True)
    shutil.copyfile(root / "scripts" / "cicd" / "run.mjs", scripts / "run.mjs")
    shutil.copyfile(root / "package.json", project / "package.json")
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    fake_uv = bin_dir / "uv"
    fake_uv.write_text(
        f"#!{sys.executable}\n"
        "import json, os, sys\n"
        "args = sys.argv[1:]\n"
        "version = args[args.index('--python') + 1]\n"
        "with open(os.environ['CI_TEST_COMMANDS'], 'a') as log:\n"
        "    log.write(json.dumps({'args': args, 'cwd': os.getcwd(), "
        "'environment': os.environ['UV_PROJECT_ENVIRONMENT']}) + '\\n')\n"
        "fail = os.environ.get('CI_TEST_FAIL')\n"
        "sys.exit(9 if fail in args and version == '3.10' else 0)\n"
    )
    fake_uv.chmod(0o755)
    commands = tmp_path / "commands.jsonl"
    env = {
        **os.environ,
        "PATH": str(bin_dir) + os.pathsep + os.environ["PATH"],
        "CI_TEST_COMMANDS": str(commands),
        "UV_PROJECT_ENVIRONMENT": str(tmp_path / "development-venv"),
    }
    return project, commands, env


def test_full_ci_runs_both_versions_in_separate_environments(ci_project):
    project, commands, env = ci_project
    result = subprocess.run(
        [shutil.which("npm"), "run", "ci:full"],
        cwd=project,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    calls = [json.loads(line) for line in commands.read_text().splitlines()]
    assert len(calls) == 8
    for version, version_calls in zip(["3.10", "3.12"], [calls[:4], calls[4:]]):
        assert [call["args"][0] for call in version_calls] == [
            "sync",
            "run",
            "run",
            "build",
        ]
        assert all(call["cwd"] == str(project) for call in version_calls)
        assert all(
            call["environment"] == str(project / ".venv-ci" / f"python-{version}")
            for call in version_calls
        )
        test_args = version_calls[2]["args"]
        assert "--disable-socket" in test_args
        assert "not external and not performance" in test_args
    report = json.loads((project / "reports" / "ci" / "summary.json").read_text())
    assert report["ok"] is True
    assert len(report["results"]) == 8
    assert all(stage["status"] == "passed" for stage in report["results"])


@pytest.mark.parametrize(
    "failed_command,stage", [("sync", "dependencies"), ("pytest", "tests")]
)
def test_ci_failure_is_reported_and_other_version_still_runs(
    ci_project, failed_command, stage
):
    project, commands, env = ci_project
    env["CI_TEST_FAIL"] = failed_command
    result = subprocess.run(
        [shutil.which("npm"), "run", "ci:full"],
        cwd=project,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 1
    report = json.loads((project / "reports" / "ci" / "summary.json").read_text())
    assert report["ok"] is False
    failed = [item for item in report["results"] if item["status"] == "failed"]
    assert [(item["python"], item["stage"], item["exit_code"]) for item in failed] == [
        ("3.10", stage, 9)
    ]
    assert all(item["status"] == "passed" for item in report["results"][4:])
    skipped = [
        item["stage"] for item in report["results"] if item["status"] == "skipped"
    ]
    assert skipped == (["lint", "tests", "build"] if stage == "dependencies" else [])
    assert len(commands.read_text().splitlines()) == (
        5 if stage == "dependencies" else 8
    )


def test_python_option_limits_ci_to_selected_version(ci_project):
    project, commands, env = ci_project
    result = subprocess.run(
        [shutil.which("npm"), "run", "ci:full", "--", "--python", "3.12"],
        cwd=project,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert len(commands.read_text().splitlines()) == 4
    report = json.loads((project / "reports" / "ci" / "summary.json").read_text())
    assert {item["python"] for item in report["results"]} == {"3.12"}
