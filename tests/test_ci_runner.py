"""Local CI runs real probe commands and propagates their exit status."""

import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

import pytest


pytestmark = [
    pytest.mark.skipif(shutil.which("node") is None, reason="Node.js is required for the CI runner"),
    pytest.mark.skipif(os.name == "nt", reason="The fake uv launcher requires POSIX shebang support"),
]


def run_ci(tmp_path, failure='', arguments=()):
    root = tmp_path / 'project'
    runner = root / 'scripts' / 'cicd' / 'run.mjs'
    runner.parent.mkdir(parents=True)
    shutil.copyfile(Path(__file__).parents[1] / 'scripts/cicd/run.mjs', runner)
    binary = tmp_path / 'bin'
    binary.mkdir()
    uv = binary / 'uv'
    uv.write_text(f'#!{sys.executable}\n' + '''import json, os, sys
with open(os.environ['CI_TEST_CALLS'], 'a') as output:
    output.write(json.dumps(sys.argv[1:]) + '\\n')
sys.exit(1 if os.environ['CI_TEST_FAIL'] in sys.argv[1:] else 0)
''')
    uv.chmod(0o755)
    calls = tmp_path / 'calls.jsonl'
    process = subprocess.run(['node', str(runner), *arguments], capture_output=True, text=True,
                             env={**os.environ, 'PATH': str(binary) + os.pathsep + os.environ['PATH'],
                                  'CI_TEST_CALLS': str(calls), 'CI_TEST_FAIL': failure})
    return process, json.loads((root / 'reports/ci/summary.json').read_text()), [
        json.loads(line) for line in calls.read_text().splitlines()]


def test_default_ci_probes_live_sources_once_after_both_python_versions(tmp_path):
    process, summary, calls = run_ci(tmp_path)
    assert process.returncode == 0 and summary['ok']
    assert len(calls) == 11
    probes = [call for call in calls if 'python' in call and '-m' in call]
    assert [call[call.index('-m') + 1] for call in probes] == [
        'scripts.check_data_sources', 'scripts.check_mootdx_interfaces', 'scripts.check_etf_pcf']
    assert all(call[call.index('--python') + 1] == '3.12' for call in probes)
    assert all('--disable-socket' not in call for call in probes)


@pytest.mark.parametrize('probe', ['check_data_sources', 'check_mootdx_interfaces', 'check_etf_pcf'])
def test_live_probe_failure_fails_ci_without_skipping_other_probes(tmp_path, probe):
    process, summary, calls = run_ci(tmp_path, f'scripts.{probe}', ('--python', '3.12'))
    assert process.returncode == 1 and not summary['ok']
    assert len(calls) == 7
    assert len([stage for stage in summary['results'] if stage['status'] == 'failed']) == 1
    assert all(stage['status'] == 'passed' for stage in summary['results'][:4])


def test_dependency_failure_reports_live_checks_skipped_and_fails_ci(tmp_path):
    process, summary, calls = run_ci(tmp_path, 'sync', ('--python', '3.10'))
    assert process.returncode == 1 and not summary['ok']
    assert len(calls) == 1
    assert all(stage['status'] == 'skipped' for stage in summary['results'][1:])
