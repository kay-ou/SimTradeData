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

Retention is per market and split by release type: `releases.json` keeps the
most recent 3 baselines (`MAX_BASELINES`) and 60 deltas (`MAX_DELTAS`) for each
market. Baselines only serve full-download fallback; incremental upgrade
eligibility is determined by the delta window plus the client budget (20
packages or 60% of the full archive size). Delta archives are never pruned from
COS, so delta edges remain available for audit or manual recovery; only
unreferenced baseline archives are removed.

## CI and Data-Source Monitoring

`.github/workflows/ci.yml` checks syntax and undefined names, runs offline pytest
regressions, and builds distributions on pushes, pull requests, and manual
dispatch, using Python 3.10 and 3.12.
`pytest-socket` forbids Python socket access during tests; tests marked `external`
or `performance` are excluded. JUnit and coverage reports are retained for 14 days.
The build does not publish a package or release.

Local CI uses the same runner as GitHub Actions:

```bash
npm run ci:full
# Optional: check one CI Python version
npm run ci:full -- --python 3.12
```

Requires Node.js 20+ (with npm) and uv; no `npm install` is needed.
By default both Python 3.10 and 3.12 run locked dependency installation,
static checks, offline tests, and distribution builds. Live health checks then run
once using the last selected Python version, with the same data-source, retained
Mootdx interface, and ETF PCF probes used by the scheduled GitHub workflow.
Unavailable, empty, stale, or invalid source responses fail local CI even if the
offline tests pass. uv can download missing
Python interpreters and dependencies. Each version uses its own environment
under `.venv-ci/`, leaving the development `.venv` in place.
Reports are written to `reports/ci/python-<version>/`, with stage statuses and
timings in `reports/ci/summary.json`; distributions go to `dist/ci/python-<version>/`.
Independent checks continue after a failure, then the command exits with code 1
if any stage failed. Dependency failure skips the remaining stages for that
Python version. Every invocation runs the selected checks again.
Live probes are separate stages, so reports distinguish code regression failures
from external source failures. They write sample reports and PCF archives under
the CI report directory, without updating the production database. Local and
GitHub results can still differ with network location, execution time, or source
availability; passing offline tests alone does not verify live interfaces.

`.github/workflows/data-sources.yml` runs independently at 07:17 and 15:17 UTC
every day and supports manual dispatch. It probes daily bars from
EastMoney, BaoStock, and yfinance separately through the production adapters,
with a single-source route so fallback cannot hide a failed provider. Each
attempt runs in a subprocess with a 90-second hard limit, with at most two
attempts. Only transient availability failures, timeouts, rate limits, and
empty responses are retried. It never downloads a full market, repairs the
database, or publishes data.

The probe checks required OHLCV fields, numeric validity, positive prices,
nonnegative volume/amount, OHLC relationships, date validity, duplicate dates,
and the latest date. Yahoo uses a date index and lacks actual traded amount, so
its probe requires OHLCV and a valid date index instead of a traded-amount field.
Freshness uses independent `exchange-calendars` XSHG/XNYS
calendars, including exchange holidays, early closes, and US daylight saving.
CN sessions become due six hours after close; US sessions become due two hours
after close. Weekends and holidays continue to expect the last due session.
An expired or unavailable calendar fails the workflow explicitly while still
checking provider availability; it never reports unverified freshness as healthy.
Keep this dependency and its lockfile updated before the calendar coverage ends.

Run all providers locally:

```bash
uv run --frozen python -m scripts.check_data_sources --json-output reports/data-sources.json
```

Run a subset or reduce the timeout:

```bash
uv run --frozen python -m scripts.check_data_sources --sources eastmoney yfinance --timeout 30 --attempts 1
```

Failures return exit code 1. The JSON report distinguishes `timeout`,
`rate_limited`, `unavailable`, `empty`, `schema_error`, `invalid_data`, and `stale`,
with expected/latest dates, attempt count, and elapsed time. GitHub Actions
includes the report in the run summary and retains it as an artifact for 30 days.
Use GitHub Actions notification settings to receive failed-workflow notifications.
There is no automatic email, issue creation, or external messaging integration.

A separate `mootdx-interfaces` job checks the retained Mootdx stock list, XDXR,
realtime quotes, minute bars, financial catalog, latest available quarterly
report, adapted snapshots, three block files, F10 text, historical transactions,
and historical intraday curves. Each interface runs in its own subprocess with a 90-second limit;
a failed quote endpoint does not prevent the financial tests. The catalog check
only verifies listing access and schema. The quarterly-content check downloads
one available quarter into a temporary directory, parses it through the existing
Affair adapter, and validates `600000` report dates, basic EPS, total assets,
goodwill, administration expense, total operating revenue, and diluted EPS.
See [financial field coverage](PTRADE_FINANCIAL_COVERAGE.md) for units,
table semantics, and refreshing previously downloaded quarters.
It does not certify every financial field or quarterly publication timeliness.
Minute bars are checked against the independent CN exchange calendar.
Block checks reject malformed codes and duplicate memberships within a file/block.
Historical transactions and curves retain vendor fields; they do not certify
Level-2 equivalence or complete history. See [TDX data access](PTRADE_DATA_COVERAGE.md#通达信获取示例).
Reports are saved as the `mootdx-interfaces` artifact, separately from daily bars.

The independent `etf-pcf` job checks official SSE/SZSE PCF for `510300.SS`
and `159919.SZ`, validates actual trading dates against the exchange calendar,
and retains dated Parquet samples and its report for 30 days. This is sample
archival, not a complete historical warehouse. See [PCF historical coverage](ETF_PCF_DATA.md).

```bash
uv run --frozen python -m scripts.check_mootdx_interfaces
uv run --frozen python -m scripts.check_mootdx_interfaces --interfaces xdxr fundamentals --timeout 90
```

TDX quote initialization changed: old SDK setup can return a two-byte `20 03`
body for otherwise compatible market-data requests. A Windows V7.71 client
uses a `0x000b` initialization request. With that initialization, the original
SDK daily, minute-bar, and realtime requests returned valid data in independent
connection tests on 2026-11-20. The extra 16 zero bytes in the desktop client's
daily request were not required.

The adapter monkeypatches `setup()` on its own TDX API instance, including
vendor automatic reconnects. It sends a `0x000b` request with a 280-byte decoded
body of zeros, encoded with the repeating mask inferred from desktop packet
padding. Independent connections returned valid data without account/device
fields. No desktop account login, captured private configuration, or GitHub
secret is required. This updates protocol initialization; it does not remove
the server connection or initialization step. The framing is based on V7.71
observations and is monitored by the retained-interface checks for future changes.
Short rejection responses fail initialization and trigger another server candidate.
The monkeypatch is instance-local and does not edit or globally patch mootdx/tdxpy.

CN responsibilities: `scripts/download.py` uses the TDX package by default for
OHLCV, including the existing benchmark import. A supplied `--tdx-source` uses
the local package instead. Mootdx downloads default to extras only; calendar and
index queries use BaoStock. SmartRouter daily stock and ETF queries now prefer
Mootdx, then fall back to EastMoney and BaoStock on errors or empty responses.
Daily pagination uses actual bar dates and includes the requested end date;
TDX-decoded ETF prices are preserved and category-9 lot volume is converted to
shares/ETF units. Adjustment is applied to the assembled history rather than
only the latest 800 bars. Quote sessions open lazily and are
checked with stock metadata at login; each retained endpoint is independently
validated by the interface job. Affair financial requests need no quote login.

The daily source workflow checks Mootdx by default, including `600000.SS`,
`510300.SS`, and `159915.SZ`. ETF traded amount divided by volume must agree
with the low/high price range within 1%, detecting price/volume scale errors.
Other providers use one representative symbol (`600000.SS` for CN and
`AAPL.US` for US). Passing does not certify every symbol,
financial statement, dividend, money-flow endpoint, or intraday endpoint.
GitHub-hosted runners may be blocked by CN providers: an availability failure
identifies a problem from that runner, not proof of a global outage. GitHub
scheduled runs can also be delayed and run only from the default branch; public
repositories may have schedules disabled after inactivity. These checks become
active once the workflow files are present on GitHub with Actions enabled.

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

## 网页管理台维护

日常操作见[网页指南](WEB_DATA_ADMIN.md)。以下是数据库恢复、性能复测和前端开发说明。

### DuckDB 索引错误的恢复

变更表采用按主键更新 `changed_at` 的写法，避免 `INSERT OR REPLACE` 的删除重插入。
二级索引仅包含 `table_name/date`，不索引每次修补都会变化的时间戳；旧索引在可正常打开的库中迁移重建。
发生 DuckDB `FatalException` 时连接已失效，不再尝试回滚而覆盖原始错误。

实际故障库曾通过只读逻辑导出重建恢复，并核对所有表记录数及行情、财务和变更表全字段校验值。连接发生致命错误时先停止写入、保留数据库与WAL，再在副本上验证恢复；不要直接覆盖健康库。

### 财务导入性能

季度财务沿用单个 DuckDB 写入进程，每 500 家公司批量入库，整季度仍在同一事务内提交。
任一批次失败时回滚该季度；不将部分入库标为完成。逐公司的字段校验、重复季度处理、
未提供字段保留和变更标记继续执行。任务结果与当前日志提供下载、解析和入库分段耗时，不保存计时历史。

可使用本地财务包复测性能，脚本只复制源包并写临时数据库，不下载或修改生产数据：

```bash
uv run python -m scripts.benchmark_financial_import data/mootdx_financial/gpcw20241231.zip --companies 100
```

增加单行去重跳过后，最新 500 家公司样本实测：逐公司写入 25.66 秒，批量写入 1.40 秒，约快 18.3 倍；
两种方式的所有财务字段结果一致。这是样本测量，不是全季度或所有机器的速度承诺。
下载并发尚未增加，当前先优化实测占主要耗时的入库阶段。

### 开发与验证

日常启动用 `npm run ui`，自动安装前端依赖和按需构建；后端入口仍为 `python -m simtradedata.web`，前端位于 `webadmin/frontend/`。
构建产物位于 `simtradedata/web/static/`，由本地服务提供。

```bash
npm run ui:build
npm --prefix webadmin/frontend exec playwright install chromium
npm run ui:test
npm run ci:full
```

浏览器测试用临时数据库和本地 ZIP 验证“覆盖检查 → 启动更新 → 真实缓存导入 → 更新后检查”，
以及手机布局、无效参数和来源检查，不下载或修改生产数据。
CI 已增加独立前端构建和浏览器测试作业。远端下载仍受数据源可用性影响。

## 发布命令

两个分发渠道都来自同一份 DuckDB/Parquet 快照，但发布内容不同：

- **GitHub Releases** 只发布完整基线包。
- **腾讯云 COS** 发布完整基线；当 COS 上一版本可以安全推进时，同时发布增量包，并通过 `releases.json` 索引供客户端发现。

```bash
# 发布 A股数据
bash scripts/release_data.sh --market cn

# 发布美股数据
bash scripts/release_data.sh --market us

# 发布版本来自 data/export/<market>/manifest.json

# 发布完整基线，并在可用时同时发布 COS 增量包
COS_SECRET_ID=... COS_SECRET_KEY=... bash scripts/release_data.sh \
  --market cn --publish-targets cos --cos-bucket BUCKET --cos-region REGION
```

