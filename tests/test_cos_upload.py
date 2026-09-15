from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import subprocess
import sys
import textwrap
import types
from pathlib import Path

import pytest


def _load_cos_upload():
    module_path = Path(__file__).resolve().parents[1] / "scripts" / "cos_upload.py"
    spec = importlib.util.spec_from_file_location("cos_upload", module_path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_upload_file_keeps_cos_object_private_by_default(monkeypatch, tmp_path):
    cos_upload = _load_cos_upload()
    archive = tmp_path / "data-cn-2026-06-26.tar.gz"
    archive.write_bytes(b"archive")
    calls = []

    def fake_cos_request(*args, **kwargs):
        calls.append({"args": args, "kwargs": kwargs})
        return 200, b""

    monkeypatch.setattr(cos_upload, "_upload_file_with_sdk", lambda *args: None)
    monkeypatch.setattr(cos_upload, "_cos_request", fake_cos_request)

    assert cos_upload.upload_file("bucket", "region", "key.tar.gz", archive, "sid", "skey") is True
    assert calls[0]["kwargs"]["public_read"] is False


def test_upload_file_uses_sdk_without_public_acl(monkeypatch, tmp_path):
    cos_upload = _load_cos_upload()
    archive = tmp_path / "data-cn-2026-06-26.tar.gz"
    archive.write_bytes(b"archive")
    calls = []
    configs = []

    class FakeClient:
        def __init__(self, config):
            self.config = config
            configs.append(config)

        def upload_file(self, **kwargs):
            calls.append(kwargs)

    fake_module = types.SimpleNamespace(
        CosConfig=lambda **kwargs: kwargs,
        CosS3Client=FakeClient,
    )
    monkeypatch.setitem(sys.modules, "qcloud_cos", fake_module)
    for env_name in (
        "COS_UPLOAD_PART_SIZE_MB",
        "COS_UPLOAD_THREADS",
        "COS_UPLOAD_TIMEOUT_SECONDS",
    ):
        monkeypatch.delenv(env_name, raising=False)

    assert cos_upload.upload_file("bucket", "region", "key.tar.gz", archive, "sid", "skey") is True
    assert calls[0]["Bucket"] == "bucket"
    assert calls[0]["Key"] == "key.tar.gz"
    assert calls[0]["PartSize"] == 64
    assert calls[0]["MAXThread"] == 1
    assert configs[0]["Timeout"] == 600
    assert "ACL" not in calls[0]
    assert "x-cos-acl" not in calls[0]


def test_releases_json_keeps_cos_object_private_by_default(monkeypatch):
    cos_upload = _load_cos_upload()
    calls = []

    def fake_cos_request(*args, **kwargs):
        calls.append({"args": args, "kwargs": kwargs})
        return 200, b""

    monkeypatch.setattr(cos_upload, "_cos_request", fake_cos_request)

    assert cos_upload._put_releases_json("bucket", "region", [], "sid", "skey") is True
    assert calls[0]["kwargs"]["public_read"] is False


@pytest.mark.parametrize(
    "fetch_error",
    [
        RuntimeError("failed to fetch releases.json (HTTP 500)"),
        json.JSONDecodeError("invalid releases index", "{", 1),
    ],
)
def test_update_releases_index_does_not_put_when_strict_fetch_fails(
    monkeypatch, capsys, fetch_error
):
    cos_upload = _load_cos_upload()
    put_calls = []

    def fail_fetch(*args, **kwargs):
        assert kwargs["strict"] is True
        raise fetch_error

    monkeypatch.setattr(cos_upload, "_fetch_releases_json", fail_fetch)
    monkeypatch.setattr(
        cos_upload,
        "_put_releases_json",
        lambda *args: put_calls.append(args) or True,
    )

    assert cos_upload._update_releases_index(
        "bucket", "region", "sid", "skey", "tag", {"tag_name": "tag"}, 30
    ) is False
    assert put_calls == []
    assert capsys.readouterr().err.startswith("ERROR:")


def test_release_data_prefers_sdk_capable_python_for_cos_upload():
    release_script = Path(__file__).resolve().parents[1] / "scripts" / "release_data.sh"
    source = release_script.read_text()

    assert "COS_UPLOAD_PYTHON" in source
    assert "sys.version_info >= (3, 10)" in source
    assert 'python3 -c "import sys; import qcloud_cos;' in source
    assert 'python3 "$@"' in source
    assert 'run_cos_python "$SCRIPT_DIR/cos_upload.py" "$@"' in source
    assert 'archive_dir=$(mktemp -d "/tmp/${tag}.XXXXXX")' in source
    assert 'local archive="$archive_dir/$archive_name"' in source
    assert 'local archive="/tmp/${archive_name}"' not in source
    assert '[[ "$PUBLISH_TARGETS" == "local" || "$PUBLISH_TARGETS" == "cos" || "$PUBLISH_TARGETS" == "all" ]]' in source


def test_empty_key_prefix_does_not_create_leading_slash(monkeypatch, tmp_path):
    cos_upload = _load_cos_upload()
    archive = tmp_path / "data-cn-2026-06-26.tar.gz"
    archive.write_bytes(b"archive")
    manifest = tmp_path / "manifest.json"
    manifest.write_text('{"market":"CN","version":"2026-06-26"}')
    uploaded_keys = []

    monkeypatch.setenv("COS_SECRET_ID", "sid")
    monkeypatch.setenv("COS_SECRET_KEY", "skey")
    monkeypatch.setattr(
        cos_upload,
        "upload_file",
        lambda bucket, region, key, file_path, secret_id, secret_key: uploaded_keys.append(key) or True,
    )
    monkeypatch.setattr(cos_upload, "_update_releases_index", lambda *args: True)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "cos_upload.py",
            "--file", str(archive),
            "--data-manifest", str(manifest),
            "--bucket", "bucket",
            "--region", "region",
        ],
    )

    cos_upload.main()

    assert uploaded_keys == ["data-cn-2026-06-26.tar.gz"]


def test_baseline_manifest_preserves_tag_and_asset_and_adds_release_metadata(
    monkeypatch, tmp_path
):
    cos_upload = _load_cos_upload()
    archive = tmp_path / "data-cn-2026-06-26.tar.gz"
    archive.write_bytes(b"archive")
    manifest = tmp_path / "manifest.json"
    manifest.write_text('{"market":"cn","version":"2026-06-26"}')
    uploaded_keys = []
    releases = []

    monkeypatch.setenv("COS_SECRET_ID", "sid")
    monkeypatch.setenv("COS_SECRET_KEY", "skey")
    monkeypatch.setattr(
        cos_upload,
        "upload_file",
        lambda bucket, region, key, file_path, secret_id, secret_key: uploaded_keys.append(key) or True,
    )
    monkeypatch.setattr(
        cos_upload,
        "_update_releases_index",
        lambda bucket, region, secret_id, secret_key, tag, entry, max_releases: releases.append(
            {"tag": tag, "entry": entry}
        )
        or True,
    )
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "cos_upload.py",
            "--file", str(archive),
            "--data-manifest", str(manifest),
            "--bucket", "bucket",
            "--region", "region",
        ],
    )

    cos_upload.main()

    assert uploaded_keys == ["data-cn-2026-06-26.tar.gz"]
    assert releases[0]["tag"] == "data-cn-2026-06-26"
    assert releases[0]["entry"]["release_type"] == "baseline"
    assert releases[0]["entry"]["market"] == "CN"
    assert releases[0]["entry"]["target_version"] == "2026-06-26"
    assert releases[0]["entry"]["assets"][0]["name"] == archive.name
    assert releases[0]["entry"]["assets"][0]["sha256"] == hashlib.sha256(
        archive.read_bytes()
    ).hexdigest()
    assert releases[0]["entry"]["assets"][0]["browser_download_url"].endswith(
        f"/{archive.name}"
    )


def test_delta_manifest_uses_version_range_for_tag_and_release_metadata(
    monkeypatch, tmp_path
):
    cos_upload = _load_cos_upload()
    archive = tmp_path / "data-cn-2026-06-25-to-2026-06-26-delta.tar.gz"
    archive.write_bytes(b"delta")
    manifest = tmp_path / "manifest.json"
    manifest.write_text(
        '{"package_format":"simtradedata_api_delta_v1","market":"cn",'
        '"from_version":"2026-06-25","to_version":"2026-06-26"}'
    )
    uploaded_keys = []
    releases = []

    monkeypatch.setenv("COS_SECRET_ID", "sid")
    monkeypatch.setenv("COS_SECRET_KEY", "skey")
    monkeypatch.setattr(
        cos_upload,
        "upload_file",
        lambda bucket, region, key, file_path, secret_id, secret_key: uploaded_keys.append(key) or True,
    )
    monkeypatch.setattr(
        cos_upload,
        "_update_releases_index",
        lambda bucket, region, secret_id, secret_key, tag, entry, max_releases: releases.append(
            {"tag": tag, "entry": entry}
        )
        or True,
    )
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "cos_upload.py",
            "--file", str(archive),
            "--data-manifest", str(manifest),
            "--bucket", "bucket",
            "--region", "region",
        ],
    )

    cos_upload.main()

    assert uploaded_keys == [archive.name]
    assert releases[0]["tag"] == "data-cn-2026-06-25-to-2026-06-26-delta"
    assert releases[0]["entry"]["release_type"] == "delta"
    assert releases[0]["entry"]["market"] == "CN"
    assert releases[0]["entry"]["base_version"] == "2026-06-25"
    assert releases[0]["entry"]["target_version"] == "2026-06-26"
    assert releases[0]["entry"]["assets"][0]["name"] == archive.name
    assert releases[0]["entry"]["assets"][0]["browser_download_url"].endswith(
        f"/{archive.name}"
    )


@pytest.mark.parametrize(
    ("manifest_data", "expected_error"),
    [
        (
            {"market": "cn", "version": "1.3.0"},
            "baseline manifest version must be an ISO date",
        ),
        (
            {"market": "cn", "version": "20260710"},
            "baseline manifest version must be an exact ISO date",
        ),
        (
            {"market": "cn", "version": "2026-W28-5"},
            "baseline manifest version must be an exact ISO date",
        ),
        (
            {
                "package_format": "simtradedata_api_delta_v1",
                "market": "cn",
                "from_version": "invalid-date",
                "to_version": "2026-07-10",
            },
            "delta manifest from_version must be an ISO date",
        ),
        (
            {
                "package_format": "simtradedata_api_delta_v1",
                "market": "cn",
                "from_version": "2026-07-09",
                "to_version": "2026-02-30",
            },
            "delta manifest to_version must be an ISO date",
        ),
        (
            {
                "package_format": "simtradedata_api_delta_v1",
                "market": "cn",
                "from_version": "2026-07-10",
                "to_version": "2026-07-10",
            },
            "delta manifest from_version must be earlier than to_version",
        ),
        (
            {
                "package_format": "simtradedata_api_delta_v1",
                "market": "cn",
                "from_version": "2026-07-11",
                "to_version": "2026-07-10",
            },
            "delta manifest from_version must be earlier than to_version",
        ),
    ],
)
def test_invalid_manifest_versions_block_archive_and_index_mutations(
    monkeypatch, tmp_path, capsys, manifest_data, expected_error
):
    cos_upload = _load_cos_upload()
    archive = tmp_path / "data.tar.gz"
    archive.write_bytes(b"archive")
    manifest = tmp_path / "manifest.json"
    manifest.write_text(json.dumps(manifest_data))
    uploads = []
    indexes = []

    monkeypatch.setenv("COS_SECRET_ID", "sid")
    monkeypatch.setenv("COS_SECRET_KEY", "skey")
    monkeypatch.setattr(
        cos_upload,
        "upload_file",
        lambda *args: uploads.append(args) or True,
    )
    monkeypatch.setattr(
        cos_upload,
        "_update_releases_index",
        lambda *args: indexes.append(args) or True,
    )
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "cos_upload.py",
            "--file",
            str(archive),
            "--data-manifest",
            str(manifest),
            "--bucket",
            "bucket",
            "--region",
            "region",
        ],
    )

    with pytest.raises(SystemExit) as exc_info:
        cos_upload.main()

    assert exc_info.value.code == 1
    assert uploads == []
    assert indexes == []
    assert expected_error in capsys.readouterr().out


def test_latest_published_version_supports_new_and_legacy_entries():
    cos_upload = _load_cos_upload()
    releases = [
        {
            "tag_name": "data-cn-2026-07-02-to-2026-07-03-delta",
            "market": "CN",
            "base_version": "2026-07-02",
            "target_version": "2026-07-03",
            "release_type": "delta",
        },
        {
            "tag_name": "data-cn-2026-07-02",
            "market": "cn",
            "target_version": "2026-07-02",
            "release_type": "baseline",
        },
        {"tag_name": "data-cn-2026-07-04"},
        {"tag_name": "data-us-2026-07-05"},
    ]

    assert cos_upload._latest_published_version(releases, "cn") == "2026-07-04"
    assert cos_upload._latest_published_version(releases, "us") == "2026-07-05"


def test_latest_published_version_ignores_delta_tags_in_legacy_parser():
    cos_upload = _load_cos_upload()
    releases = [
        {"tag_name": "data-cn-2026-07-09-to-2026-07-10-delta"},
        {"tag_name": "data-cn-2026-07-08"},
        {
            "tag_name": "unrelated",
            "market": "CN",
            "target_version": "not-a-date",
            "release_type": "baseline",
        },
    ]

    assert cos_upload._latest_published_version(releases, "CN") == "2026-07-08"


def test_print_latest_version_mode_does_not_require_upload_files(
    monkeypatch, capsys
):
    cos_upload = _load_cos_upload()
    releases = [
        {
            "market": "CN",
            "target_version": "2026-07-08",
            "release_type": "baseline",
        }
    ]
    monkeypatch.setenv("COS_SECRET_ID", "sid")
    monkeypatch.setenv("COS_SECRET_KEY", "skey")
    monkeypatch.setattr(
        cos_upload, "_fetch_releases_json", lambda *args, **kwargs: releases
    )
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "cos_upload.py",
            "--print-latest-version",
            "--market",
            "cn",
            "--bucket",
            "bucket",
            "--region",
            "region",
        ],
    )

    cos_upload.main()

    assert capsys.readouterr().out == "2026-07-08\n"


def test_print_latest_version_reports_malformed_releases_json_without_traceback(
    monkeypatch, capsys
):
    cos_upload = _load_cos_upload()
    error = json.JSONDecodeError("invalid releases index", "{", 1)
    monkeypatch.setenv("COS_SECRET_ID", "sid")
    monkeypatch.setenv("COS_SECRET_KEY", "skey")
    monkeypatch.setattr(
        cos_upload,
        "_fetch_releases_json",
        lambda *args, **kwargs: (_ for _ in ()).throw(error),
    )
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "cos_upload.py",
            "--print-latest-version",
            "--market",
            "cn",
            "--bucket",
            "bucket",
            "--region",
            "region",
        ],
    )

    with pytest.raises(SystemExit) as exc_info:
        cos_upload.main()

    assert exc_info.value.code == 1
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err.startswith("ERROR: invalid releases index")


@pytest.mark.parametrize(
    ("flag", "expected_uploads", "expected_indexes"),
    [
        ("--skip-index", ["data-cn-2026-06-26.tar.gz"], []),
        ("--index-only", [], ["data-cn-2026-06-26"]),
    ],
)
def test_upload_phase_flags_separate_archive_and_index_updates(
    monkeypatch, tmp_path, flag, expected_uploads, expected_indexes
):
    cos_upload = _load_cos_upload()
    archive = tmp_path / "data-cn-2026-06-26.tar.gz"
    archive.write_bytes(b"archive")
    manifest = tmp_path / "manifest.json"
    manifest.write_text('{"market":"cn","version":"2026-06-26"}')
    uploads = []
    indexes = []

    monkeypatch.setenv("COS_SECRET_ID", "sid")
    monkeypatch.setenv("COS_SECRET_KEY", "skey")
    monkeypatch.setattr(
        cos_upload,
        "upload_file",
        lambda bucket, region, key, file_path, secret_id, secret_key: uploads.append(key)
        or True,
    )
    monkeypatch.setattr(
        cos_upload,
        "_update_releases_index",
        lambda bucket, region, secret_id, secret_key, tag, entry, max_releases: indexes.append(
            tag
        )
        or True,
    )
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "cos_upload.py",
            flag,
            "--file",
            str(archive),
            "--data-manifest",
            str(manifest),
            "--bucket",
            "bucket",
            "--region",
            "region",
        ],
    )

    cos_upload.main()

    assert uploads == expected_uploads
    assert indexes == expected_indexes


def test_release_data_publishes_cos_delta_before_baseline():
    release_script = Path(__file__).resolve().parents[1] / "scripts" / "release_data.sh"
    source = release_script.read_text()

    lookup = '--print-latest-version --market "$market"'
    export = 'scripts/api_export_delta.py --market "$market" --last-sync "$cos_base_version"'
    delta_name = 'data-${market}-${cos_base_version}-to-${version}-delta.tar.gz'
    delta_upload = '--file "$delta_archive"'
    baseline_upload = '--file "$archive"'

    assert lookup in source
    assert source.index(lookup) < source.index("poetry run python scripts/export_parquet.py")
    assert export in source
    assert 'tar -xOf "$delta_archive" manifest.json' in source
    assert delta_name in source
    assert "up_to_date" in source
    assert "fallback_to_baseline" in source
    assert "pipeline_busy" in source
    assert 'manifest.get("tables")' in source
    assert source.index(delta_upload) < source.index(baseline_upload)
    assert 'local checksum="$archive_dir/${tag}.sha256"' in source
    assert "digest = hashlib.sha256()" in source
    assert 'gh release upload "$tag" "$archive" "$checksum" --clobber' in source
    assert '"$archive" "$checksum" || github_ok=false' in source


def _write_executable(path: Path, source: str) -> None:
    path.write_text(textwrap.dedent(source).lstrip("\n"))
    path.chmod(0o755)


def test_write_executable_places_shebang_on_first_line(tmp_path):
    script = tmp_path / "stub"

    _write_executable(script, """
        #!/usr/bin/env bash
        exit 0
    """)

    assert script.read_bytes().startswith(b"#!/usr/bin/env bash\n")


def _run_release_script(
    tmp_path: Path,
    *,
    base_version: str,
    delta_mode: str,
    local_version: str = "2026-07-10",
    baseline_mode: str = "success",
    lookup_mode: str = "success",
    delta_index_mode: str = "success",
    prune_mode: str = "success",
):
    project = tmp_path / "project"
    scripts = project / "scripts"
    bin_dir = tmp_path / "bin"
    scripts.mkdir(parents=True)
    bin_dir.mkdir()
    source_script = Path(__file__).resolve().parents[1] / "scripts" / "release_data.sh"
    release_script = scripts / "release_data.sh"
    release_script.write_text(source_script.read_text())

    _write_executable(
        bin_dir / "fake-python",
        """
        #!/usr/bin/env bash
        set -euo pipefail
        if [[ "$1" == */cos_upload.py ]]; then
          shift
          args=("$@")
          for arg in "$@"; do
            if [[ "$arg" == "--print-latest-version" ]]; then
              if [[ "$LOOKUP_MODE" == "fail" ]]; then exit 1; fi
              printf '%s\n' "$FAKE_BASE_VERSION"
              exit 0
            fi
            if [[ "$arg" == "--prune-orphans" ]]; then
              if [[ "$PRUNE_MODE" == "fail" ]]; then exit 1; fi
              printf 'prune\n' >> "$PRUNE_LOG"
              exit 0
            fi
          done
          file=""
          phase="normal"
          while [[ $# -gt 0 ]]; do
            if [[ "$1" == "--file" ]]; then file=$(basename "$2"); shift 2; continue; fi
            if [[ "$1" == "--skip-index" ]]; then phase="skip-index"; fi
            if [[ "$1" == "--index-only" ]]; then phase="index-only"; fi
            shift
          done
          if [[ -n "$file" ]]; then
            manifest=""
            set -- "${args[@]}"
            while [[ $# -gt 0 ]]; do
              if [[ "$1" == "--data-manifest" ]]; then manifest="$2"; break; fi
              shift
            done
            metadata=$("$REAL_PYTHON" -c 'import json,sys
m=json.load(open(sys.argv[1])); delta=m.get("package_format")=="simtradedata_api_delta_v1"
tag=("data-{}-{}-to-{}-delta".format(m["market"].lower(),m["from_version"],m["to_version"]) if delta else "data-{}-{}".format(m["market"].lower(),m["version"]))
print("|".join((tag, "delta" if delta else "baseline", m.get("from_version", ""), m.get("to_version", m.get("version", "")))))' "$manifest")
            printf '%s|%s|%s\n' "$phase" "$metadata" "$file" >> "$UPLOAD_LOG"
            if [[ "$file" == "data-cn-2026-07-10.tar.gz" && "$BASELINE_MODE" == "fail" ]]; then
              exit 1
            fi
            if [[ "$phase" == "index-only" && "$DELTA_INDEX_MODE" == "fail" ]]; then
              exit 1
            fi
            exit 0
          fi
          exit 2
        fi
        exec "$REAL_PYTHON" "$@"
        """,
    )
    _write_executable(
        bin_dir / "poetry",
        """
        #!/usr/bin/env bash
        set -euo pipefail
        [[ "$1" == "run" && "$2" == "python" ]]
        shift 2
        script="$1"
        shift
        if [[ "$script" == "scripts/export_parquet.py" ]]; then
          market=""
          while [[ $# -gt 0 ]]; do
            if [[ "$1" == "--market" ]]; then market="$2"; shift 2; else shift; fi
          done
          mkdir -p "$PWD/data/export/$market"
          printf '{"market":"%s","version":"%s"}\n' "$market" "$LOCAL_VERSION" > "$PWD/data/export/$market/manifest.json"
          exit 0
        fi
        if [[ "$script" == "scripts/api_export_delta.py" ]]; then
          output=""
          while [[ $# -gt 0 ]]; do
            if [[ "$1" == "--output" ]]; then output="$2"; shift 2; else shift; fi
          done
          package="${output}.contents"
          mkdir -p "$package"
          if [[ "$DELTA_MODE" == "success" ]]; then
            printf '%s\n' '{"package_format":"simtradedata_api_delta_v1","market":"cn","from_version":"2026-07-09","to_version":"2026-07-10","up_to_date":false,"fallback_to_baseline":false,"pipeline_busy":false,"tables":[{"table":"stocks"}]}' > "$package/manifest.json"
          elif [[ "$DELTA_MODE" == "invalid-date" ]]; then
            printf '%s\n' '{"package_format":"simtradedata_api_delta_v1","market":"cn","from_version":"invalid-date","to_version":"2026-07-10","up_to_date":false,"fallback_to_baseline":false,"pipeline_busy":false,"tables":[{"table":"stocks"}]}' > "$package/manifest.json"
          elif [[ "$DELTA_MODE" == "equal-date" ]]; then
            printf '%s\n' '{"package_format":"simtradedata_api_delta_v1","market":"cn","from_version":"2026-07-10","to_version":"2026-07-10","up_to_date":false,"fallback_to_baseline":false,"pipeline_busy":false,"tables":[{"table":"stocks"}]}' > "$package/manifest.json"
          elif [[ "$DELTA_MODE" == "reversed-date" ]]; then
            printf '%s\n' '{"package_format":"simtradedata_api_delta_v1","market":"cn","from_version":"2026-07-11","to_version":"2026-07-10","up_to_date":false,"fallback_to_baseline":false,"pipeline_busy":false,"tables":[{"table":"stocks"}]}' > "$package/manifest.json"
          elif [[ "$DELTA_MODE" == "fallback" ]]; then
            printf '%s\n' '{"market":"cn","from_version":"2026-07-09","to_version":"2026-07-10","up_to_date":false,"fallback_to_baseline":true,"pipeline_busy":false,"tables":[]}' > "$package/manifest.json"
          else
            printf '%s\n' 'not-json' > "$package/manifest.json"
          fi
          /usr/bin/tar -czf "$output" -C "$package" manifest.json
          rm -rf "$package"
          exit 0
        fi
        exec "$REAL_PYTHON" "$script" "$@"
        """,
    )
    _write_executable(
        bin_dir / "mktemp",
        """
        #!/usr/bin/env bash
        set -euo pipefail
        printf '%s\n' "$*" >> "$MKTEMP_LOG"
        if [[ "$1" == "-d" ]]; then
          path="$HARNESS_TMP/archive-dir"
          mkdir -p "$path"
        else
          path="$HARNESS_TMP/archive-dir/delta-manifest.tmp.json"
          : > "$path"
        fi
        printf '%s\n' "$path"
        """,
    )

    upload_log = tmp_path / "uploads.log"
    mktemp_log = tmp_path / "mktemp.log"
    env = {
        **os.environ,
        "PATH": f"{bin_dir}:/usr/bin:/bin",
        "COS_UPLOAD_PYTHON": str(bin_dir / "fake-python"),
        "COS_SECRET_ID": "sid",
        "COS_SECRET_KEY": "skey",
        "FAKE_BASE_VERSION": base_version,
        "LOCAL_VERSION": local_version,
        "DELTA_MODE": delta_mode,
        "BASELINE_MODE": baseline_mode,
        "LOOKUP_MODE": lookup_mode,
        "DELTA_INDEX_MODE": delta_index_mode,
        "UPLOAD_LOG": str(upload_log),
        "MKTEMP_LOG": str(mktemp_log),
        "PRUNE_LOG": str(tmp_path / "prune.log"),
        "PRUNE_MODE": prune_mode,
        "HARNESS_TMP": str(tmp_path),
        "REAL_PYTHON": sys.executable,
    }
    result = subprocess.run(
        [
            "bash",
            str(release_script),
            "--market",
            "cn",
            "--publish-targets",
            "cos",
            "--cos-bucket",
            "bucket",
            "--cos-region",
            "region",
            "--local-release-dir",
            str(tmp_path / "local"),
        ],
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )
    uploads = upload_log.read_text().splitlines() if upload_log.exists() else []
    mktemp_calls = mktemp_log.read_text().splitlines() if mktemp_log.exists() else []
    return result, uploads, mktemp_calls, tmp_path / "archive-dir"


def test_release_script_uploads_delta_before_baseline_and_cleans_temp_files(tmp_path):
    result, uploads, mktemp_calls, archive_dir = _run_release_script(
        tmp_path, base_version="2026-07-09", delta_mode="success"
    )

    assert result.returncode == 0, result.stdout + result.stderr
    assert uploads == [
        "skip-index|data-cn-2026-07-09-to-2026-07-10-delta|delta|2026-07-09|2026-07-10|data-cn-2026-07-09-to-2026-07-10-delta.tar.gz",
        "normal|data-cn-2026-07-10|baseline||2026-07-10|data-cn-2026-07-10.tar.gz",
        "index-only|data-cn-2026-07-09-to-2026-07-10-delta|delta|2026-07-09|2026-07-10|data-cn-2026-07-09-to-2026-07-10-delta.tar.gz",
    ]
    assert len(mktemp_calls) == 2
    assert not archive_dir.exists()


def test_release_script_fallback_still_uploads_baseline_and_cleans_temp_files(
    tmp_path,
):
    result, uploads, mktemp_calls, archive_dir = _run_release_script(
        tmp_path, base_version="2026-07-09", delta_mode="fallback"
    )

    assert result.returncode == 0, result.stdout + result.stderr
    assert uploads == [
        "normal|data-cn-2026-07-10|baseline||2026-07-10|data-cn-2026-07-10.tar.gz"
    ]
    assert len(mktemp_calls) == 2
    assert not archive_dir.exists()


def test_release_script_date_error_fails_cos_publish_but_uploads_baseline(tmp_path):
    result, uploads, _, archive_dir = _run_release_script(
        tmp_path, base_version="invalid-date", delta_mode="success"
    )

    assert result.returncode != 0
    assert uploads == []
    assert "date comparison failed" in result.stdout
    assert not archive_dir.exists()


def test_release_script_invalid_local_version_on_empty_bucket_blocks_all_cos_mutation(
    tmp_path,
):
    result, uploads, _, archive_dir = _run_release_script(
        tmp_path,
        base_version="",
        local_version="invalid-date",
        delta_mode="success",
    )

    assert result.returncode != 0
    assert uploads == []
    assert "local manifest version is not an ISO date" in result.stdout
    assert not archive_dir.exists()


def test_release_script_invalid_delta_version_blocks_all_cos_mutation(tmp_path):
    result, uploads, _, archive_dir = _run_release_script(
        tmp_path,
        base_version="2026-07-09",
        delta_mode="invalid-date",
    )

    assert result.returncode != 0
    assert uploads == []
    assert "delta manifest versions are not ISO dates" in result.stdout
    assert not archive_dir.exists()


@pytest.mark.parametrize(
    ("base_version", "local_version", "delta_mode"),
    [
        ("", "20260710", "success"),
        ("", "2026-W28-5", "success"),
        ("2026-07-09", "2026-07-10", "equal-date"),
        ("2026-07-09", "2026-07-10", "reversed-date"),
    ],
)
def test_release_script_rejects_noncanonical_or_nonincreasing_versions_before_cos_mutation(
    tmp_path, base_version, local_version, delta_mode
):
    result, uploads, _, archive_dir = _run_release_script(
        tmp_path,
        base_version=base_version,
        local_version=local_version,
        delta_mode=delta_mode,
    )

    assert result.returncode != 0
    assert uploads == []
    assert not archive_dir.exists()


def test_release_script_does_not_advertise_delta_when_baseline_upload_fails(tmp_path):
    result, uploads, _, archive_dir = _run_release_script(
        tmp_path,
        base_version="2026-07-09",
        delta_mode="success",
        baseline_mode="fail",
    )

    assert result.returncode != 0
    assert uploads == [
        "skip-index|data-cn-2026-07-09-to-2026-07-10-delta|delta|2026-07-09|2026-07-10|data-cn-2026-07-09-to-2026-07-10-delta.tar.gz",
        "normal|data-cn-2026-07-10|baseline||2026-07-10|data-cn-2026-07-10.tar.gz",
    ]
    assert not archive_dir.exists()


def test_release_script_lookup_failure_blocks_all_cos_mutation(tmp_path):
    result, uploads, _, archive_dir = _run_release_script(
        tmp_path,
        base_version="2026-07-09",
        delta_mode="success",
        lookup_mode="fail",
    )

    assert result.returncode != 0
    assert uploads == []
    assert "failed to determine latest COS version" in result.stdout
    assert not archive_dir.exists()


def test_release_script_future_cos_version_blocks_all_cos_mutation(tmp_path):
    result, uploads, _, archive_dir = _run_release_script(
        tmp_path,
        base_version="2026-07-11",
        delta_mode="success",
    )

    assert result.returncode != 0
    assert uploads == []
    assert "newer than local version" in result.stdout
    assert not archive_dir.exists()


def test_release_script_equal_cos_version_allows_idempotent_baseline_publish(tmp_path):
    result, uploads, _, archive_dir = _run_release_script(
        tmp_path,
        base_version="2026-07-10",
        delta_mode="success",
    )

    assert result.returncode == 0, result.stdout + result.stderr
    assert uploads == [
        "normal|data-cn-2026-07-10|baseline||2026-07-10|data-cn-2026-07-10.tar.gz"
    ]
    assert not archive_dir.exists()


def test_release_script_delta_index_failure_keeps_baseline_published_but_fails(
    tmp_path,
):
    result, uploads, _, archive_dir = _run_release_script(
        tmp_path,
        base_version="2026-07-09",
        delta_mode="success",
        delta_index_mode="fail",
    )

    assert result.returncode != 0
    assert uploads == [
        "skip-index|data-cn-2026-07-09-to-2026-07-10-delta|delta|2026-07-09|2026-07-10|data-cn-2026-07-09-to-2026-07-10-delta.tar.gz",
        "normal|data-cn-2026-07-10|baseline||2026-07-10|data-cn-2026-07-10.tar.gz",
        "index-only|data-cn-2026-07-09-to-2026-07-10-delta|delta|2026-07-09|2026-07-10|data-cn-2026-07-09-to-2026-07-10-delta.tar.gz",
    ]
    assert not archive_dir.exists()


def test_update_releases_index_drops_dangling_delta_after_trim(monkeypatch, capsys):
    cos_upload = _load_cos_upload()
    releases = [
        {
            "tag_name": "data-cn-2026-06-10",
            "release_type": "baseline",
            "target_version": "2026-06-10",
        },
        {
            "tag_name": "data-cn-2026-06-11-to-2026-06-12-delta",
            "release_type": "delta",
            "base_version": "2026-06-11",
            "target_version": "2026-06-12",
        },
        {
            "tag_name": "data-cn-2026-06-12",
            "release_type": "baseline",
            "target_version": "2026-06-12",
        },
    ]
    put_calls = []
    monkeypatch.setattr(cos_upload, "_fetch_releases_json", lambda *a, **k: releases)
    monkeypatch.setattr(
        cos_upload, "_put_releases_json", lambda *a: put_calls.append(a) or True
    )
    entry = {
        "tag_name": "data-cn-2026-06-13",
        "release_type": "baseline",
        "target_version": "2026-06-13",
    }

    assert (
        cos_upload._update_releases_index(
            "bucket", "region", "sid", "skey", entry["tag_name"], entry, 3
        )
        is True
    )

    final = put_calls[0][2]
    assert [r["tag_name"] for r in final] == [
        "data-cn-2026-06-13",
        "data-cn-2026-06-10",
    ]
    assert "Dropped 1 dangling delta" in capsys.readouterr().out


def test_update_releases_index_keeps_delta_with_surviving_base(monkeypatch):
    cos_upload = _load_cos_upload()
    releases = [
        {
            "tag_name": "data-cn-2026-06-11",
            "release_type": "baseline",
            "target_version": "2026-06-11",
        },
        {
            "tag_name": "data-cn-2026-06-11-to-2026-06-12-delta",
            "release_type": "delta",
            "base_version": "2026-06-11",
            "target_version": "2026-06-12",
        },
        {
            "tag_name": "data-cn-2026-06-12",
            "release_type": "baseline",
            "target_version": "2026-06-12",
        },
    ]
    put_calls = []
    monkeypatch.setattr(cos_upload, "_fetch_releases_json", lambda *a, **k: releases)
    monkeypatch.setattr(
        cos_upload, "_put_releases_json", lambda *a: put_calls.append(a) or True
    )
    entry = {
        "tag_name": "data-cn-2026-06-13",
        "release_type": "baseline",
        "target_version": "2026-06-13",
    }

    cos_upload._update_releases_index(
        "bucket", "region", "sid", "skey", entry["tag_name"], entry, 10
    )

    final = put_calls[0][2]
    assert [r["tag_name"] for r in final] == [
        "data-cn-2026-06-13",
        "data-cn-2026-06-11",
        "data-cn-2026-06-11-to-2026-06-12-delta",
        "data-cn-2026-06-12",
    ]


def test_prune_orphans_deletes_only_unreferenced_old_archives(monkeypatch):
    cos_upload = _load_cos_upload()
    now = cos_upload.dt.datetime.now(cos_upload.dt.timezone.utc)
    old_ts = (now - cos_upload.dt.timedelta(hours=48)).strftime(
        "%Y-%m-%dT%H:%M:%S.000Z"
    )
    recent_ts = (now - cos_upload.dt.timedelta(hours=1)).strftime(
        "%Y-%m-%dT%H:%M:%S.000Z"
    )

    releases = [
        {
            "release_type": "baseline",
            "target_version": "2026-09-11",
            "assets": [{"name": "data-cn-2026-09-11.tar.gz"}],
        }
    ]
    monkeypatch.setattr(cos_upload, "_fetch_releases_json", lambda *a, **k: releases)

    deletes = []

    class FakeClient:
        def __init__(self, config):
            self.config = config

        def list_objects(self, **kwargs):
            return {
                "IsTruncated": "false",
                "Contents": [
                    {
                        "Key": "data-cn-2026-09-11.tar.gz",
                        "Size": "1000",
                        "LastModified": old_ts,
                    },
                    {
                        "Key": "data-cn-2026-06-26.tar.gz",
                        "Size": "1000",
                        "LastModified": old_ts,
                    },
                    {
                        "Key": "data-cn-2026-06-25-to-2026-06-26-delta.tar.gz",
                        "Size": "10",
                        "LastModified": old_ts,
                    },
                    {
                        "Key": "data-cn-2026-09-02-floor-20050509.tar.gz",
                        "Size": "1000",
                        "LastModified": old_ts,
                    },
                    {
                        "Key": "data-cn-2026-09-12.tar.gz",
                        "Size": "1000",
                        "LastModified": recent_ts,
                    },
                    {"Key": "releases.json", "Size": "1", "LastModified": old_ts},
                    {
                        "Key": "zhengguan/latest/windows.exe",
                        "Size": "1000",
                        "LastModified": old_ts,
                    },
                    {
                        "Key": "strategy-packages/abc/pkg.stpkg",
                        "Size": "1",
                        "LastModified": old_ts,
                    },
                ],
            }

        def delete_objects(self, **kwargs):
            deletes.append(kwargs)
            return {}

    fake_module = types.SimpleNamespace(
        CosConfig=lambda **kwargs: kwargs,
        CosS3Client=FakeClient,
    )
    monkeypatch.setitem(sys.modules, "qcloud_cos", fake_module)

    cos_upload._prune_orphans("bucket", "region", "sid", "skey", "", 24)

    deleted_keys = [
        obj["Key"] for batch in deletes for obj in batch["Delete"]["Object"]
    ]
    assert deleted_keys == [
        "data-cn-2026-06-26.tar.gz",
        "data-cn-2026-06-25-to-2026-06-26-delta.tar.gz",
        "data-cn-2026-09-02-floor-20050509.tar.gz",
    ]


def test_release_script_prunes_orphans_after_publish(tmp_path):
    result, _, _, archive_dir = _run_release_script(
        tmp_path, base_version="2026-07-09", delta_mode="success"
    )

    assert result.returncode == 0, result.stdout + result.stderr
    assert (tmp_path / "prune.log").read_text().splitlines() == ["prune"]
    assert not archive_dir.exists()


def test_release_script_prune_failure_does_not_fail_publish(tmp_path):
    result, _, _, archive_dir = _run_release_script(
        tmp_path,
        base_version="2026-07-09",
        delta_mode="success",
        prune_mode="fail",
    )

    assert result.returncode == 0, result.stdout + result.stderr
    assert "WARNING: COS prune failed" in result.stdout
    assert not archive_dir.exists()
