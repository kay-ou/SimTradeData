# Data Quality Operations

Goal: improve data quality without putting heavy repair jobs on the daily
release path.

## Principles

- Daily jobs keep data fresh, complete, and publishable.
- Heavy quality repair jobs run separately from daily publishing.
- BaoStock-heavy jobs must not run in the default daily path.
- Every automated task must be listed in `ops/data-quality-tasks.yaml`.
- systemd templates live under `ops/systemd/` and match the YAML task names.
- Host-specific paths belong in `/etc/simtradedata.env`, not in unit files.
- Failed jobs do not loop forever; keep the last known-good release.

## Task Layers

| Task | Frequency | Publishes | Purpose |
|---|---:|---:|---|
| `daily_cn_data` | Trading days 22:30 | Yes | Market, valuation, and status refresh; pre/post integrity; local or COS release |
| `weekly_cn_quality_repair` | Manual first, then Saturday 02:00 after validation | No | Historical quality repair for `bonus_ps`, exrights, and fundamentals |
| `monthly_cn_reconcile` | First Sunday monthly | No | Read-only quality report for long-term drift |
| `manual_cn_recovery` | Manual | As needed | Gap fill, rollback, or baseline rebuild |

## Daily Job

Example systemd checks:

```bash
systemctl status simtradedata-daily-cn.timer
systemctl status simtradedata-daily-cn.service
```

Service environment should explicitly set:

```text
SIMTRADE_DATA_DIR=/path/to/SimTradeData
MARKET=cn
PUBLISH_TARGETS=local
DOWNLOAD_ATTEMPTS=3
INTEGRITY_STRICT=1
```

For COS publishing, use `PUBLISH_TARGETS=cos` and provide the bucket, region,
key prefix, and credentials.

GitHub Releases and COS use the same exported Parquet snapshot but have different
distribution roles:

- GitHub publishes full baseline archives only.
- COS publishes the baseline and, when the existing COS version can be advanced,
  a delta archive indexed by `releases.json`.

The COS release is deliberately fail-closed. Before any upload, the publisher
must read and validate the existing index, reject malformed JSON or invalid
dates, and stop if the remote version is newer than the local version. A valid
delta is uploaded without indexing it first; the baseline archive and its index
entry are then published; only after that succeeds is the delta entry added to
`releases.json`. This ordering prevents clients from discovering a delta whose
target baseline was not published successfully.

## Status Check

From the repository root:

```bash
python3 scripts/ops_status.py
```

The command prints task layer, systemd enabled/active state, and the next timer
time. On machines without systemd, it prints configuration status only.
Weekly repair and monthly reconcile templates are listed in YAML for tracking,
but should be enabled only after runtime and data-source pressure are acceptable.

## Daily Gate

Command from `ops/data-quality-tasks.yaml`:

```bash
MARKET=cn PUBLISH_TARGETS=local DOWNLOAD_ATTEMPTS=3 INTEGRITY_STRICT=1 bash scripts/run_daily.sh
```

Before publishing, the job must pass:

- DuckDB latest date exists.
- Active stock universe is not empty.
- Active stock market-data coverage is 100%.
- Active stock valuation coverage is 100%.
- Manifest version matches the DuckDB latest date.

## Weekly Repair

Purpose: improve historical quality without blocking daily publishing.

Typical work:

- `bonus_ps` precision correction.
- Exrights gap fill.
- Fundamentals gap fill or rebuild.

Run manually first. Add a timer only after runtime and data-source pressure are
known. After repair, produce integrity/report output; do not publish
automatically.

## Monthly Reconcile

Read-only task. If it finds problems, open a data-quality repair item; do not
mutate data from this task.

```bash
.venv/bin/python scripts/check_integrity.py --market cn --strict --json-output logs/validation/monthly_cn_reconcile.json
```

## Failure Handling

- Daily job failure: do not publish; inspect latest `logs/daily/*.log` and
  integrity JSON.
- BaoStock-related failure: rely on the bounded daily attempts first; if all
  attempts fail, wait for the next schedule or run a bounded manual repair.
- Bad published data: move manifest/latest pointer back to the previous version
  and keep logs for diagnosis.
- COS index lookup or validation failure: do not upload or rewrite release
  metadata. Fix the remote index or version mismatch first, then rerun the
  bounded publish job.
- Partial COS publish: an unindexed delta archive is safe to leave in object
  storage; clients cannot discover it. Do not add its index entry until the
  corresponding baseline publication has succeeded.
