#!/usr/bin/env bash
# Daily data refresh and release pipeline for SimTradeData.
#
# Usage (from crontab or systemd timer):
#   MARKET=cn PUBLISH_TARGETS=local \
#   bash scripts/run_daily.sh
#
# Environment variables:
#   MARKET              Market to refresh: cn | us (default: cn)
#   PUBLISH_TARGETS     local | github | cos | all (default: github)
#   SIMTRADE_DATA_DIR   Path to SimTradeData repo (default: ../SimTradeData)
#   COS_BUCKET          COS bucket name (required for cos target)
#   COS_REGION          COS region (required for cos target)
#   COS_KEY_PREFIX      COS key prefix (default: "")
#   LOCAL_RELEASE_DIR   Local artifact directory for local target (default: data/releases)
#   ALERT_WEBHOOK_URL   Webhook URL for failure alerts (optional)
#   LOCK_FILE           Path to lock file (default: /tmp/simtradedata_daily.lock)
#   LOG_DIR             Directory for run logs (default: logs/daily)
#   LOG_RETENTION_DAYS  Days to keep logs (default: 30)
#   LOCAL_RELEASE_KEEP  Local release tarballs to keep (default: 16)
#   DISK_ALERT_PERCENT  Alert when data dir filesystem usage exceeds this % (default: 85)
#   DOWNLOAD_ATTEMPTS   Download + pre-release integrity attempts before giving up/no-op (default: 1)
#   RETRY_INTERVAL_SECONDS Seconds between download retries (default: 1800)
#   INTEGRITY_STRICT    Run integrity gates before/after release (default: 1)

set -euo pipefail

if [[ "${1:-}" == "-h" || "${1:-}" == "--help" ]]; then
  sed -n '2,20p' "$0" | sed 's/^# \{0,1\}//'
  exit 0
fi

# ── Configuration ───────────────────────────────────────────────────
MARKET="$(echo "${MARKET:-cn}" | tr '[:upper:]' '[:lower:]')"
PUBLISH_TARGETS="${PUBLISH_TARGETS:-github}"
SIMTRADE_DATA_DIR="${SIMTRADE_DATA_DIR:-$(cd "$(dirname "$0")/.." && pwd)}"
COS_BUCKET="${COS_BUCKET:-}"
COS_REGION="${COS_REGION:-}"
COS_KEY_PREFIX="${COS_KEY_PREFIX:-}"
ALERT_WEBHOOK_URL="${ALERT_WEBHOOK_URL:-}"
LOCK_FILE="${LOCK_FILE:-/tmp/simtradedata_daily_${MARKET}.lock}"
LOG_DIR="${LOG_DIR:-$SIMTRADE_DATA_DIR/logs/daily}"
LOG_RETENTION_DAYS="${LOG_RETENTION_DAYS:-30}"
LOCAL_RELEASE_KEEP="${LOCAL_RELEASE_KEEP:-16}"
DISK_ALERT_PERCENT="${DISK_ALERT_PERCENT:-85}"
DOWNLOAD_ATTEMPTS="${DOWNLOAD_ATTEMPTS:-1}"
RETRY_INTERVAL_SECONDS="${RETRY_INTERVAL_SECONDS:-1800}"
INTEGRITY_STRICT="${INTEGRITY_STRICT:-1}"

if [[ "$MARKET" != "cn" && "$MARKET" != "us" ]]; then
  echo "ERROR: MARKET must be cn or us"
  exit 1
fi

if ! [[ "$DOWNLOAD_ATTEMPTS" =~ ^[1-9][0-9]*$ ]]; then
  echo "ERROR: DOWNLOAD_ATTEMPTS must be a positive integer"
  exit 1
fi

if ! [[ "$RETRY_INTERVAL_SECONDS" =~ ^[0-9]+$ ]]; then
  echo "ERROR: RETRY_INTERVAL_SECONDS must be a non-negative integer"
  exit 1
fi

if [[ "$INTEGRITY_STRICT" != "0" && "$INTEGRITY_STRICT" != "1" ]]; then
  echo "ERROR: INTEGRITY_STRICT must be 0 or 1"
  exit 1
fi

DUCKDB_FILE="$SIMTRADE_DATA_DIR/data/${MARKET}.duckdb"

# ── Acquire single-instance lock ────────────────────────────────────
exec 200>"$LOCK_FILE"
if ! flock -n 200; then
  echo "[$(date '+%Y-%m-%d %H:%M:%S')] Another instance holds $LOCK_FILE, exiting."
  exit 0
fi

# ── Setup logging ───────────────────────────────────────────────────
mkdir -p "$LOG_DIR"
LOG_FILE="$LOG_DIR/$(date +%Y%m%d_%H%M%S)_${MARKET}.log"
exec > >(tee -a "$LOG_FILE") 2>&1

log() { echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*"; }

alert() {
  local msg="$*"
  log "ALERT: $msg"
  if [[ -n "$ALERT_WEBHOOK_URL" ]]; then
    curl -s -X POST "$ALERT_WEBHOOK_URL" \
      -H "Content-Type: application/json" \
      -d "{\"text\":\"SimTradeData daily pipeline [${MARKET}]: ${msg}\"}" \
      >/dev/null 2>&1 || true
  fi
}

cleanup() {
  find "$LOG_DIR" -name "*.log" -mtime "+$LOG_RETENTION_DAYS" -delete 2>/dev/null || true
  local release_dir="${LOCAL_RELEASE_DIR:-$SIMTRADE_DATA_DIR/data/releases}"
  ls -1 "$release_dir"/data-"$MARKET"-*.tar.gz 2>/dev/null | sort \
    | head -n -"$LOCAL_RELEASE_KEEP" | xargs -r rm -f
  rm -f "$SIMTRADE_DATA_DIR/data/downloads/hsjday.zip"
}

tracks_local_release_version() {
  [[ "$PUBLISH_TARGETS" == "local" || "$PUBLISH_TARGETS" == "cos" || "$PUBLISH_TARGETS" == "all" ]]
}

run_download() {
  if [[ "$MARKET" == "us" ]]; then
    poetry run python scripts/download_us.py
  else
    poetry run python scripts/download.py --tdx-download --skip-mootdx-ohlcv --skip-bonus-fix
  fi
}

run_pre_release_integrity() {
  if [[ "$INTEGRITY_STRICT" != "1" ]]; then
    return 0
  fi

  PRE_RELEASE_INTEGRITY_REPORT="$LOG_DIR/$(date +%Y%m%d_%H%M%S)_${MARKET}_pre_release_integrity.json"
  log "--- Running pre-release integrity gate ---"
  if poetry run python scripts/check_integrity.py \
    --db-path "$DUCKDB_FILE" \
    --market "$MARKET" \
    --target-date "$NEW_VERSION" \
    --json-output "$PRE_RELEASE_INTEGRITY_REPORT" \
    --strict; then
    log "Pre-release integrity verified: $PRE_RELEASE_INTEGRITY_REPORT"
    return 0
  fi

  return 1
}

# ── Determine DuckDB path by market ─────────────────────────────────
get_version() {
  local market="$1"
  cd "$SIMTRADE_DATA_DIR"
  poetry run python -c "
import duckdb
from pathlib import Path
from simtradedata.utils.paths import DATA_PATH

db_map = {'cn': DATA_PATH / 'cn.duckdb', 'us': DATA_PATH / 'us.duckdb'}
db_path = str(db_map.get('$market', db_map['cn']))
p = Path(db_path)
if not p.exists():
    print('')
    exit(0)
conn = duckdb.connect(db_path, read_only=True)
try:
    result = conn.execute('SELECT MAX(date) FROM stocks').fetchone()
    print(result[0] if result and result[0] else '')
finally:
    conn.close()
"
}

get_released_version() {
  local market="$1"
  local release_dir="${LOCAL_RELEASE_DIR:-$SIMTRADE_DATA_DIR/data/releases}"
  local latest=""
  local file base version

  shopt -s nullglob
  for file in "$release_dir"/data-"$market"-*.tar.gz; do
    base="$(basename "$file")"
    version="${base#data-$market-}"
    version="${version%.tar.gz}"
    if [[ -z "$latest" || "$version" > "$latest" ]]; then
      latest="$version"
    fi
  done
  shopt -u nullglob

  printf '%s\n' "$latest"
}

# ── Main pipeline ───────────────────────────────────────────────────
log "=== SimTradeData Daily Pipeline Start ==="
log "  Market:          $MARKET"
log "  Publish targets: $PUBLISH_TARGETS"
log "  Data dir:        $SIMTRADE_DATA_DIR"
log "  Log file:        $LOG_FILE"
log "  Download tries:  $DOWNLOAD_ATTEMPTS"
log "  Retry interval:  ${RETRY_INTERVAL_SECONDS}s"
log "  Integrity gate:  $INTEGRITY_STRICT"

# Disk usage guard: alert early when the data filesystem is nearly full.
DISK_USAGE_PERCENT=$(df -P "$SIMTRADE_DATA_DIR" | awk 'NR==2 {gsub(/%/,""); print $5}')
log "  Disk usage:      ${DISK_USAGE_PERCENT}%"
if (( DISK_USAGE_PERCENT > DISK_ALERT_PERCENT )); then
  alert "disk usage ${DISK_USAGE_PERCENT}% exceeds ${DISK_ALERT_PERCENT}%"
fi

cd "$SIMTRADE_DATA_DIR"

# 1. Snapshot version before download
OLD_VERSION=$(get_version "$MARKET")
log "Version before download: ${OLD_VERSION:-none}"
TRACK_LOCAL_RELEASE_VERSION=0
OLD_RELEASE_VERSION=""
if tracks_local_release_version; then
  TRACK_LOCAL_RELEASE_VERSION=1
  OLD_RELEASE_VERSION=$(get_released_version "$MARKET")
  log "Latest local release: ${OLD_RELEASE_VERSION:-none}"
fi

# 2. Run download with retry. BaoStock and other free sources can publish late;
# retry hard failures, unchanged versions, and incomplete pre-release integrity.
NEW_VERSION=""
DOWNLOAD_RC=0
PRE_RELEASE_INTEGRITY_OK=0
PRE_RELEASE_INTEGRITY_REPORT=""
for attempt in $(seq 1 "$DOWNLOAD_ATTEMPTS"); do
  log "--- Running download attempt ${attempt}/${DOWNLOAD_ATTEMPTS} ---"
  if run_download; then
    DOWNLOAD_RC=0
    NEW_VERSION=$(get_version "$MARKET")
    log "Version after download:  ${NEW_VERSION:-none}"

    if [[ -z "$NEW_VERSION" ]]; then
      DOWNLOAD_RC=1
      log "No data in stocks table after download"
    elif [[ "$OLD_VERSION" != "$NEW_VERSION" ]]; then
      log "New data detected: ${OLD_VERSION:-none} → $NEW_VERSION"
      if run_pre_release_integrity; then
        PRE_RELEASE_INTEGRITY_OK=1
        break
      fi
      DOWNLOAD_RC=1
      log "Pre-release integrity failed on attempt ${attempt}/${DOWNLOAD_ATTEMPTS}"
    elif [[ "$TRACK_LOCAL_RELEASE_VERSION" == "1" && "$OLD_RELEASE_VERSION" != "$NEW_VERSION" && "$attempt" -eq "$DOWNLOAD_ATTEMPTS" ]]; then
      log "Data version unchanged ($NEW_VERSION), but local release is ${OLD_RELEASE_VERSION:-none}; final attempt reached, continuing to integrity gate and release."
      if run_pre_release_integrity; then
        PRE_RELEASE_INTEGRITY_OK=1
        break
      fi
      DOWNLOAD_RC=1
      log "Pre-release integrity failed on attempt ${attempt}/${DOWNLOAD_ATTEMPTS}"
    else
      log "No new data yet (version unchanged: $NEW_VERSION)"
    fi
  else
    DOWNLOAD_RC=$?
    log "Download attempt ${attempt}/${DOWNLOAD_ATTEMPTS} failed (exit code: ${DOWNLOAD_RC})"
  fi

  if [[ "$attempt" -lt "$DOWNLOAD_ATTEMPTS" ]]; then
    log "Retrying in ${RETRY_INTERVAL_SECONDS}s..."
    sleep "$RETRY_INTERVAL_SECONDS"
  fi
done

if [[ -z "$NEW_VERSION" ]]; then
  alert "No data in stocks table after download"
  cleanup
  exit 1
fi

if [[ "$DOWNLOAD_RC" -ne 0 ]]; then
  if [[ -n "$PRE_RELEASE_INTEGRITY_REPORT" ]]; then
    alert "pre-release integrity gate failed after ${DOWNLOAD_ATTEMPTS} attempts (last report: $PRE_RELEASE_INTEGRITY_REPORT)"
    cleanup
    exit 1
  fi
  alert "download failed after ${DOWNLOAD_ATTEMPTS} attempts (last exit code: ${DOWNLOAD_RC})"
  cleanup
  exit "$DOWNLOAD_RC"
fi

if [[ "$PRE_RELEASE_INTEGRITY_OK" != "1" ]]; then
  log "No new data after ${DOWNLOAD_ATTEMPTS} attempts (version unchanged: $NEW_VERSION). Skipping release."
  log "=== Pipeline Complete (no-op) ==="
  cleanup
  exit 0
fi

# 5. Run release pipeline (export + package + publish)
log "--- Running release_data.sh ---"
RELEASE_ARGS="--market $MARKET --publish-targets $PUBLISH_TARGETS"
if [[ "$PUBLISH_TARGETS" == "cos" || "$PUBLISH_TARGETS" == "all" ]]; then
  if [[ -n "$COS_BUCKET" ]]; then RELEASE_ARGS="$RELEASE_ARGS --cos-bucket $COS_BUCKET"; fi
  if [[ -n "$COS_REGION" ]]; then RELEASE_ARGS="$RELEASE_ARGS --cos-region $COS_REGION"; fi
  if [[ -n "$COS_KEY_PREFIX" ]]; then RELEASE_ARGS="$RELEASE_ARGS --cos-key-prefix $COS_KEY_PREFIX"; fi
fi
if [[ "$PUBLISH_TARGETS" == "local" && -n "${LOCAL_RELEASE_DIR:-}" ]]; then
  RELEASE_ARGS="$RELEASE_ARGS --local-release-dir $LOCAL_RELEASE_DIR"
fi

if ! bash scripts/release_data.sh $RELEASE_ARGS; then
  alert "release_data.sh failed"
  cleanup
  exit 1
fi

# 6. Freshness gate: verify exported manifest version matches DB
MANIFEST_FILE="$SIMTRADE_DATA_DIR/data/export/$MARKET/manifest.json"
if [[ -f "$MANIFEST_FILE" ]]; then
  MANIFEST_VERSION=$(python3 -c "import json; print(json.load(open('$MANIFEST_FILE')).get('version',''))")
  if [[ "$MANIFEST_VERSION" != "$NEW_VERSION" ]]; then
    alert "Freshness FAILED: manifest=${MANIFEST_VERSION}, db=${NEW_VERSION}"
    cleanup
    exit 1
  fi
  log "Freshness verified: ${MANIFEST_VERSION}"
else
  alert "Manifest missing: $MANIFEST_FILE"
  cleanup
  exit 1
fi

if [[ "$INTEGRITY_STRICT" == "1" ]]; then
  INTEGRITY_REPORT="$LOG_DIR/$(date +%Y%m%d_%H%M%S)_${MARKET}_post_release_integrity.json"
  log "--- Running post-release integrity gate ---"
  if ! poetry run python scripts/check_integrity.py \
    --db-path "$DUCKDB_FILE" \
    --market "$MARKET" \
    --target-date "$NEW_VERSION" \
    --export-dir "$SIMTRADE_DATA_DIR/data/export/$MARKET" \
    --json-output "$INTEGRITY_REPORT" \
    --strict; then
    alert "post-release integrity gate failed (report: $INTEGRITY_REPORT)"
    cleanup
    exit 1
  fi
  log "Post-release integrity verified: $INTEGRITY_REPORT"
fi

log "=== Pipeline Complete (published $NEW_VERSION) ==="
cleanup
