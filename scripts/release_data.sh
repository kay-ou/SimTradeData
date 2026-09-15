#!/usr/bin/env bash
# Export data from DuckDB and release locally, to GitHub, or to Tencent COS.
# Usage: bash scripts/release_data.sh [options]
#
# This script:
# 1. Runs export_parquet.py → data/export/{market}/
# 2. Packages into a single tar.gz
# 3. Publishes locally, to GitHub Release, and/or Tencent COS
#
# Prerequisites:
#   Local:   no external dependencies
#   GitHub:  poetry install, gh auth login
#   COS:     COS_SECRET_ID / COS_SECRET_KEY env vars set

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

# ── Defaults ────────────────────────────────────────────────────────
MARKET="cn"
PUBLISH_TARGETS="github"          # local | github | cos | all
COS_BUCKET="${COS_BUCKET:-}"
COS_REGION="${COS_REGION:-}"
COS_KEY_PREFIX="${COS_KEY_PREFIX:-}"
LOCAL_RELEASE_DIR="${LOCAL_RELEASE_DIR:-$PROJECT_ROOT/data/releases}"

# ── Parse arguments ─────────────────────────────────────────────────
while [[ $# -gt 0 ]]; do
  case "$1" in
    -h|--help)
      sed -n '2,13p' "$0" | sed 's/^# \{0,1\}//'
      exit 0
      ;;
    --market)          MARKET="$2"; shift 2 ;;
    --publish-targets) PUBLISH_TARGETS="$2"; shift 2 ;;
    --cos-bucket)      COS_BUCKET="$2"; shift 2 ;;
    --cos-region)      COS_REGION="$2"; shift 2 ;;
    --cos-key-prefix)  COS_KEY_PREFIX="$2"; shift 2 ;;
    --local-release-dir) LOCAL_RELEASE_DIR="$2"; shift 2 ;;
    *) echo "Unknown argument: $1"; exit 1 ;;
  esac
done

MARKET=$(echo "$MARKET" | tr '[:upper:]' '[:lower:]')
if [[ "$MARKET" != "cn" && "$MARKET" != "us" && "$MARKET" != "all" ]]; then
  echo "ERROR: --market must be cn, us, or all"
  exit 1
fi

if [[ "$PUBLISH_TARGETS" != "local" && "$PUBLISH_TARGETS" != "github" && "$PUBLISH_TARGETS" != "cos" && "$PUBLISH_TARGETS" != "all" ]]; then
  echo "ERROR: --publish-targets must be local, github, cos, or all"
  exit 1
fi

run_cos_python() {
  if [[ -n "${COS_UPLOAD_PYTHON:-}" ]]; then
    "$COS_UPLOAD_PYTHON" "$@"
  elif command -v python3 >/dev/null 2>&1 && python3 -c "import sys; import qcloud_cos; raise SystemExit(0 if sys.version_info >= (3, 10) else 1)" >/dev/null 2>&1; then
    python3 "$@"
  else
    poetry run python "$@"
  fi
}

run_cos_upload() {
  run_cos_python "$SCRIPT_DIR/cos_upload.py" "$@"
}

is_earlier_date() {
  run_cos_python -c 'import datetime as d, sys
try:
    remote = d.date.fromisoformat(sys.argv[1])
    local = d.date.fromisoformat(sys.argv[2])
except (IndexError, ValueError):
    raise SystemExit(2)
if remote.isoformat() != sys.argv[1] or local.isoformat() != sys.argv[2]:
    raise SystemExit(2)
raise SystemExit(0 if remote < local else 1 if remote == local else 3)' "$1" "$2" 2>/dev/null
}

is_exact_iso_date() {
  run_cos_python -c 'import datetime as d, sys
value = sys.argv[1]
try:
    parsed = d.date.fromisoformat(value)
except (IndexError, ValueError):
    raise SystemExit(2)
raise SystemExit(0 if parsed.isoformat() == value else 2)' "$1" 2>/dev/null
}

delta_is_publishable() {
  run_cos_python -c 'import json, sys; manifest=json.load(sys.stdin); raise SystemExit(0 if not manifest.get("up_to_date") and not manifest.get("fallback_to_baseline") and not manifest.get("pipeline_busy") and manifest.get("tables") else 2)'
}

delta_versions_are_valid() {
  run_cos_python -c 'import datetime as d, json, sys
manifest = json.load(sys.stdin)
base_value = manifest["from_version"]
target_value = manifest["to_version"]
base = d.date.fromisoformat(base_value)
target = d.date.fromisoformat(target_value)
raise SystemExit(0 if base.isoformat() == base_value and target.isoformat() == target_value and base < target else 2)'
}

# ── Publish single market ───────────────────────────────────────────
release_market() {
  local market="$1"
  local export_dir="$PROJECT_ROOT/data/export/$market"
  local cos_base_version=""
  local cos_lookup_ok=true

  if [[ "$PUBLISH_TARGETS" == "cos" || "$PUBLISH_TARGETS" == "all" ]]; then
    if [[ -z "$COS_BUCKET" ]]; then
      echo "ERROR: --cos-bucket or COS_BUCKET env var required for COS publish"
      cos_lookup_ok=false
    elif [[ -z "$COS_REGION" ]]; then
      echo "ERROR: --cos-region or COS_REGION env var required for COS publish"
      cos_lookup_ok=false
    elif ! cos_base_version=$(run_cos_upload --print-latest-version --market "$market" --bucket "$COS_BUCKET" --region "$COS_REGION"); then
      echo "ERROR: failed to determine latest COS version for $market"
      cos_lookup_ok=false
    fi
  fi

  # 1. Export
  echo "=== Exporting $market data ==="
  cd "$PROJECT_ROOT"
  poetry run python scripts/export_parquet.py --market "$market"

  # Data manifest produced by export_parquet.py
  local data_manifest="$export_dir/manifest.json"
  if [ ! -f "$data_manifest" ]; then
    echo "ERROR: Export did not produce manifest.json"
    return 1
  fi

  local version
  version=$(run_cos_python -c "import json; print(json.load(open('$data_manifest'))['version'])")
  if ! is_exact_iso_date "$version"; then
    echo "ERROR: local manifest version is not an ISO date (YYYY-MM-DD): $version"
    return 1
  fi
  local tag="data-${market}-${version}"
  local archive_name="${tag}.tar.gz"
  local archive_dir
  archive_dir=$(mktemp -d "/tmp/${tag}.XXXXXX")
  trap 'rm -rf "$archive_dir"' RETURN EXIT
  local archive="$archive_dir/$archive_name"
  local checksum="$archive_dir/${tag}.sha256"
  local local_archive="$LOCAL_RELEASE_DIR/$archive_name"

  # 2. Package
  echo ""
  echo "=== Packaging ${market} ${version} ==="
  tar -czf "$archive" -C "$export_dir" .
  run_cos_python -c 'import hashlib, pathlib, sys
path = pathlib.Path(sys.argv[1])
digest = hashlib.sha256()
with path.open("rb") as source:
    for chunk in iter(lambda: source.read(1024 * 1024), b""):
        digest.update(chunk)
print(f"{digest.hexdigest()}  {sys.argv[2]}")' "$archive" "$archive_name" > "$checksum"

  local size
  size=$(du -h "$archive" | cut -f1)
  echo "  -> $archive ($size)"

  local local_ok=true
  local github_ok=true
  local cos_ok="$cos_lookup_ok"

  # 3a. Local artifact / authorized index mirror
  if [[ "$PUBLISH_TARGETS" == "local" || "$PUBLISH_TARGETS" == "cos" || "$PUBLISH_TARGETS" == "all" ]]; then
    echo ""
    echo "=== Publishing locally ==="
    mkdir -p "$LOCAL_RELEASE_DIR"
    cp "$archive" "$local_archive" || local_ok=false
    if $local_ok; then
      echo "  -> $local_archive"
    else
      echo "  ERROR: local publish failed"
    fi
  fi

  # 3b. GitHub Release
  if [[ "$PUBLISH_TARGETS" == "github" || "$PUBLISH_TARGETS" == "all" ]]; then
    echo ""
    echo "=== Publishing to GitHub ==="
    if gh release view "$tag" >/dev/null 2>&1; then
      echo "  Release $tag exists, updating..."
      gh release upload "$tag" "$archive" "$checksum" --clobber || github_ok=false
    else
      gh release create "$tag" \
        --title "SimTradeData ${market} ${version}" \
        --notes "Data date: ${version} (${market})" \
        "$archive" "$checksum" || github_ok=false
    fi
    if $github_ok; then
      echo "  -> $(gh release view "$tag" --json url -q .url 2>/dev/null || echo "uploaded")"
    else
      echo "  ERROR: GitHub release failed"
    fi
  fi

  # 3c. Tencent COS
  if [[ "$PUBLISH_TARGETS" == "cos" || "$PUBLISH_TARGETS" == "all" ]]; then
    echo ""
    echo "=== Publishing to COS ==="
    if [[ -z "$COS_BUCKET" ]]; then
      echo "  ERROR: --cos-bucket or COS_BUCKET env var required for COS publish"
      cos_ok=false
    elif [[ -z "$COS_REGION" ]]; then
      echo "  ERROR: --cos-region or COS_REGION env var required for COS publish"
      cos_ok=false
    elif ! $cos_lookup_ok; then
      :
    else
      local delta_uploaded=false
      local cos_mutation_allowed=true
      if [[ -n "$cos_base_version" ]]; then
        local date_status=0
        is_earlier_date "$cos_base_version" "$version" || date_status=$?
        if [[ "$date_status" -eq 0 ]]; then
          local delta_archive_name="data-${market}-${cos_base_version}-to-${version}-delta.tar.gz"
          local delta_archive="$archive_dir/$delta_archive_name"
          local delta_manifest
          if delta_manifest=$(mktemp "$archive_dir/delta-manifest.XXXXXX.json"); then
            echo "  Building COS delta from $cos_base_version to $version..."
            if poetry run python scripts/api_export_delta.py --market "$market" --last-sync "$cos_base_version" --output "$delta_archive"; then
              if tar -xOf "$delta_archive" manifest.json > "$delta_manifest"; then
                if delta_versions_are_valid < "$delta_manifest" 2>/dev/null; then
                  local delta_manifest_status=0
                  delta_is_publishable < "$delta_manifest" || delta_manifest_status=$?
                  if [[ "$delta_manifest_status" -eq 0 ]]; then
                    run_cos_upload \
                      --skip-index \
                      --file "$delta_archive" \
                      --data-manifest "$delta_manifest" \
                      --bucket "$COS_BUCKET" \
                      --region "$COS_REGION" \
                      --key-prefix "$COS_KEY_PREFIX" && delta_uploaded=true || cos_ok=false
                  elif [[ "$delta_manifest_status" -eq 2 ]]; then
                    echo "  Delta unavailable; continuing with baseline upload"
                  else
                    echo "  ERROR: invalid delta manifest"
                    cos_ok=false
                  fi
                else
                  echo "  ERROR: delta manifest versions are not ISO dates (YYYY-MM-DD)"
                  cos_ok=false
                  cos_mutation_allowed=false
                fi
              else
                echo "  ERROR: failed to read delta manifest"
                cos_ok=false
              fi
            else
              echo "  ERROR: delta export failed"
              cos_ok=false
            fi
          else
            echo "  ERROR: failed to create delta manifest temp file"
            cos_ok=false
          fi
        elif [[ "$date_status" -eq 2 ]]; then
          echo "  ERROR: COS base/current date comparison failed"
          cos_ok=false
          cos_mutation_allowed=false
        elif [[ "$date_status" -eq 3 ]]; then
          echo "  ERROR: COS version $cos_base_version is newer than local version $version"
          cos_ok=false
          cos_mutation_allowed=false
        fi
      fi

      if $cos_mutation_allowed && run_cos_upload \
          --file "$archive" \
          --data-manifest "$data_manifest" \
          --bucket "$COS_BUCKET" \
          --region "$COS_REGION" \
          --key-prefix "$COS_KEY_PREFIX"; then
        if $delta_uploaded; then
          run_cos_upload \
            --index-only \
            --file "$delta_archive" \
            --data-manifest "$delta_manifest" \
            --bucket "$COS_BUCKET" \
            --region "$COS_REGION" \
            --key-prefix "$COS_KEY_PREFIX" || cos_ok=false
        fi
      elif $cos_mutation_allowed; then
        cos_ok=false
      fi
    fi
  fi

  # 4. Cleanup
  rm -rf "$archive_dir"
  trap - RETURN EXIT

  # 5. Summary
  echo ""
  echo "=== Release Summary for $market ==="
  if [[ "$PUBLISH_TARGETS" == "local" ]]; then
    echo "  Local:  $($local_ok && echo 'OK' || echo 'FAILED')"
  fi
  if [[ "$PUBLISH_TARGETS" == "github" || "$PUBLISH_TARGETS" == "all" ]]; then
    echo "  GitHub: $($github_ok && echo 'OK' || echo 'FAILED')"
  fi
  if [[ "$PUBLISH_TARGETS" == "cos" || "$PUBLISH_TARGETS" == "all" ]]; then
    echo "  COS:    $($cos_ok && echo 'OK' || echo 'FAILED')"
  fi

  # Fail if any requested target failed
  if ! $local_ok || ! $github_ok || ! $cos_ok; then
    return 1
  fi
  echo ""
}

# ── Main ─────────────────────────────────────────────────────────────
if [ "$MARKET" = "all" ]; then
  release_market "cn"
  release_market "us"
else
  release_market "$MARKET"
fi

# Prune COS data archives that are no longer referenced by releases.json.
# Best-effort: a failed prune must not fail an otherwise successful publish.
if [[ "$PUBLISH_TARGETS" == "cos" || "$PUBLISH_TARGETS" == "all" ]]; then
  echo ""
  echo "=== Pruning orphaned COS data archives ==="
  prune_args=(--prune-orphans --bucket "$COS_BUCKET" --region "$COS_REGION")
  if [[ -n "$COS_KEY_PREFIX" ]]; then
    prune_args+=(--key-prefix "$COS_KEY_PREFIX")
  fi
  run_cos_upload "${prune_args[@]}" || echo "  WARNING: COS prune failed"
fi
