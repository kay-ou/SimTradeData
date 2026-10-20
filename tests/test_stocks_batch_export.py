"""Equivalence tests for the batched _export_stocks_batch.

The batched implementation must produce byte-identical per-symbol output
to the previous all-at-once materialization (which exhausted the 7GB
customer box). The reference SQL below is the pre-batching implementation.
"""

import pandas as pd
import pyarrow.parquet as pq
import pytest

from simtradedata.writers.duckdb_writer import DuckDBWriter

# Pre-batching reference: gap-fill for ALL symbols in one materialization.
_REFERENCE_SQL = """
CREATE OR REPLACE TEMP TABLE _ref_filled AS
WITH trade_cal AS (
    SELECT DISTINCT date FROM stocks
    UNION
    SELECT date FROM trade_days
),
lifespans AS (
    SELECT s0.symbol, MIN(s0.date) AS first_date, MAX(s0.date) AS last_date
    FROM stocks s0 {filter} GROUP BY s0.symbol
),
joined AS (
    SELECT
        ls.symbol,
        tc.date,
        CASE WHEN s.volume > 0 THEN s.open END AS open,
        CASE WHEN s.volume > 0 THEN s.close END AS close,
        CASE WHEN s.volume > 0 THEN s.high END AS high,
        CASE WHEN s.volume > 0 THEN s.low END AS low,
        CASE WHEN s.volume > 0 THEN s.preclose END AS preclose,
        COALESCE(s.volume, 0) AS volume,
        COALESCE(s.money, 0.0) AS money
    FROM lifespans ls
    CROSS JOIN trade_cal tc
    LEFT JOIN stocks s ON s.symbol = ls.symbol AND s.date = tc.date
    WHERE tc.date >= ls.first_date AND tc.date <= ls.last_date
),
gap_filled AS (
    SELECT
        symbol, date,
        COALESCE(open, last_value(close IGNORE NULLS) OVER w) AS open,
        COALESCE(close, last_value(close IGNORE NULLS) OVER w) AS close,
        COALESCE(high, last_value(close IGNORE NULLS) OVER w) AS high,
        COALESCE(low, last_value(close IGNORE NULLS) OVER w) AS low,
        preclose, volume, money
    FROM joined
    WINDOW w AS (PARTITION BY symbol ORDER BY date ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW)
),
with_lag AS (
    SELECT
        symbol, date,
        open, close, high, low,
        LAG(close) OVER (PARTITION BY symbol ORDER BY date) AS lag_close,
        last_value(CASE WHEN volume > 0 THEN date END IGNORE NULLS) OVER (
            PARTITION BY symbol ORDER BY date
            ROWS BETWEEN UNBOUNDED PRECEDING AND 1 PRECEDING
        ) AS last_active_date,
        preclose AS stored_preclose,
        volume, money
    FROM gap_filled
),
adj AS (
    SELECT
        wl.symbol, wl.date,
        SUM(COALESCE(ex.bonus_ps, 0)
            - COALESCE(ex.rationed_px, 0) * COALESCE(ex.rationed_ps, 0)
        ) AS total_deduction,
        EXP(SUM(LN(
            1 + COALESCE(ex.allotted_ps, 0) + COALESCE(ex.rationed_ps, 0)
        ))) AS total_divisor,
        COUNT(ex.date) AS event_count
    FROM with_lag wl
    INNER JOIN exrights ex ON ex.symbol = wl.symbol
        AND ex.date > wl.last_active_date AND ex.date <= wl.date
    GROUP BY wl.symbol, wl.date
)
SELECT
    wl.symbol,
    wl.date::TIMESTAMP_NS AS date,
    wl.open, wl.close, wl.high, wl.low,
    CASE
        WHEN adj.event_count > 0 AND wl.lag_close IS NOT NULL
             AND wl.volume > 0 THEN
            ROUND(
                (wl.lag_close - adj.total_deduction)
                / adj.total_divisor,
                2)
        ELSE COALESCE(wl.lag_close, wl.stored_preclose)
    END AS preclose,
    wl.volume, wl.money
FROM with_lag wl
LEFT JOIN adj ON adj.symbol = wl.symbol AND adj.date = wl.date
"""


def _seed(conn):
    # trade_days includes 2025-01-08 where NO stock trades -> gap-fill day
    conn.execute("""
        INSERT INTO trade_days VALUES
        ('2025-01-06'), ('2025-01-07'), ('2025-01-08'),
        ('2025-01-09'), ('2025-01-10')
    """)
    # 000001.SZ: missing 01-08 entirely (suspension -> gap-filled)
    # 000002.SZ: trades 01-08 with volume=0 (suspended row -> NULL OHLC)
    # 300001.SZ: ChiNext limit branch
    # 600000.SS: exrights event on 01-09
    # 000300.SS: index row, no security_type -> filtered out by CN filter
    conn.execute("""
        INSERT INTO stocks (symbol, date, open, close, high, low, preclose, volume, money)
        VALUES
        ('000001.SZ', '2025-01-06', 10.0, 10.5, 10.6, 9.9, 9.8, 1000, 10500.0),
        ('000001.SZ', '2025-01-07', 10.6, 11.0, 11.1, 10.5, 10.5, 1100, 12000.0),
        ('000001.SZ', '2025-01-09', 11.1, 11.2, 11.3, 11.0, 11.0, 900, 10100.0),
        ('000001.SZ', '2025-01-10', 11.2, 11.5, 11.6, 11.1, 11.2, 950, 10900.0),
        ('000002.SZ', '2025-01-06', 20.0, 20.5, 20.6, 19.9, 19.8, 500, 10250.0),
        ('000002.SZ', '2025-01-07', 20.6, 21.0, 21.1, 20.5, 20.5, 600, 12600.0),
        ('000002.SZ', '2025-01-08', 21.1, 21.1, 21.1, 21.1, 21.0, 0, 0.0),
        ('000002.SZ', '2025-01-09', 21.2, 21.3, 21.4, 21.1, 21.1, 550, 11700.0),
        ('000002.SZ', '2025-01-10', 21.3, 21.6, 21.7, 21.2, 21.3, 580, 12500.0),
        ('300001.SZ', '2025-01-06', 30.0, 30.5, 30.6, 29.9, 29.8, 300, 9150.0),
        ('300001.SZ', '2025-01-07', 30.6, 31.0, 31.1, 30.5, 30.5, 310, 9600.0),
        ('300001.SZ', '2025-01-08', 31.1, 31.2, 31.3, 31.0, 31.0, 320, 9980.0),
        ('300001.SZ', '2025-01-09', 31.2, 31.4, 31.5, 31.1, 31.2, 330, 10360.0),
        ('300001.SZ', '2025-01-10', 31.4, 31.7, 31.8, 31.3, 31.4, 340, 10780.0),
        ('600000.SS', '2025-01-06', 8.0, 8.5, 8.6, 7.9, 7.8, 2000, 17000.0),
        ('600000.SS', '2025-01-07', 8.6, 9.0, 9.1, 8.5, 8.5, 2100, 18900.0),
        ('600000.SS', '2025-01-08', 9.1, 9.2, 9.3, 9.0, 9.0, 2050, 18900.0),
        ('600000.SS', '2025-01-09', 9.2, 9.4, 9.5, 9.1, 9.2, 2200, 20700.0),
        ('600000.SS', '2025-01-10', 9.4, 9.6, 9.7, 9.3, 9.4, 2300, 22100.0),
        ('000300.SS', '2025-01-06', 4000.0, 4010.0, 4015.0, 3995.0, 3990.0, 0, 0.0),
        ('000300.SS', '2025-01-07', 4010.0, 4020.0, 4025.0, 4005.0, 4010.0, 0, 0.0)
    """)
    conn.execute("""
        INSERT INTO exrights (symbol, date, allotted_ps, rationed_ps, rationed_px, bonus_ps)
        VALUES ('600000.SS', '2025-01-09', 1.0, 0.0, 0.0, 0.0)
    """)
    # security_type present -> official-first filter branch (LIKE %.SZ/%.SS + type '1')
    conn.execute("""
        INSERT INTO stock_metadata (symbol, security_type) VALUES
        ('000001.SZ', '1'), ('000002.SZ', '1'),
        ('300001.SZ', '1'), ('600000.SS', '1')
    """)


@pytest.mark.unit
class TestStocksBatchExport:
    @pytest.fixture(scope="class")
    def exported(self, tmp_path_factory):
        """Run the batched export once; all tests share the output."""
        out_dir = tmp_path_factory.mktemp("export") / "stocks"
        out_dir.mkdir()
        writer = DuckDBWriter(db_path=":memory:")
        _seed(writer.conn)
        writer._export_stocks_batch(out_dir, market="cn")
        writer.close()
        return out_dir

    def setup_method(self):
        self.writer = DuckDBWriter(db_path=":memory:")
        _seed(self.writer.conn)

    def teardown_method(self):
        self.writer.close()

    def _reference_frame(self, symbol: str) -> pd.DataFrame:
        """Materialize the reference SQL once, filter per symbol."""
        if not hasattr(self, "_ref_all"):
            filter_sql = self.writer._cn_stock_filter_sql("s0.symbol")
            self.writer.conn.execute(
                _REFERENCE_SQL.format(filter=f"WHERE {filter_sql}")
            )
            self._ref_all = self.writer.conn.execute(
                "SELECT * FROM _ref_filled ORDER BY symbol, date"
            ).fetchdf()
        df = self._ref_all[self._ref_all["symbol"] == symbol]
        return df.drop(columns="symbol").reset_index(drop=True)

    def test_batch_matches_reference_all_symbols(self, exported):
        files = sorted(p.name for p in exported.glob("*.parquet"))
        # Index row filtered out; only the 4 metadata-typed stocks exported
        assert files == [
            "000001.SZ.parquet", "000002.SZ.parquet",
            "300001.SZ.parquet", "600000.SS.parquet",
        ]

        for f in files:
            symbol = f.removesuffix(".parquet")
            actual = pq.read_table(exported / f).to_pandas()
            expected = self._reference_frame(symbol)
            # Batch SQL and reference SQL share column order; limits columns
            # are appended by COPY and checked separately below.
            common = list(expected.columns)
            pd.testing.assert_frame_equal(
                actual[common].reset_index(drop=True),
                expected.reset_index(drop=True),
                check_dtype=True,
                check_exact=True,
                obj=f"batch vs reference for {symbol}",
            )

    def test_gap_fill_and_suspension_rows(self, exported):
        # 000001.SZ: 01-08 has no stocks row -> gap-filled from 01-07 close
        df = pq.read_table(exported / "000001.SZ.parquet").to_pandas()
        assert len(df) == 5
        gap = df[df["date"] == "2025-01-08"].iloc[0]
        assert gap["close"] == 11.0
        assert gap["open"] == 11.0
        assert gap["volume"] == 0

        # 000002.SZ: 01-08 traded with volume=0 -> OHLC NULLed by the CASE,
        # then filled with the last traded close (21.0 from 01-07), not with
        # the stored (possibly ex-rights distorted) values.
        df2 = pq.read_table(exported / "000002.SZ.parquet").to_pandas()
        sus = df2[df2["date"] == "2025-01-08"].iloc[0]
        assert sus["close"] == 21.0
        assert sus["volume"] == 0

    def test_exrights_adjusts_preclose(self, exported):
        # 600000.SS: exrights on 01-09, allotted_ps=1.0. The event lands in
        # the 01-09 row's window (last_active_date=01-08 < ex.date=01-09 <=
        # date): preclose(01-09) = lag_close(01-08) / EXP(LN(1+1)) = 4.6.
        # The 01-10 row has no event in window -> preclose = lag_close = 9.4.
        df = pq.read_table(exported / "600000.SS.parquet").to_pandas()
        row09 = df[df["date"] == "2025-01-09"].iloc[0]
        row10 = df[df["date"] == "2025-01-10"].iloc[0]
        assert row09["preclose"] == pytest.approx(4.6)
        assert row10["preclose"] == pytest.approx(9.4)

    def test_price_limits_chiNext_vs_normal(self, exported):
        # 300001.SZ -> 20% band; 000001.SZ -> 10% band
        cy = pq.read_table(exported / "300001.SZ.parquet").to_pandas()
        normal = pq.read_table(exported / "000001.SZ.parquet").to_pandas()
        cy_row = cy[cy["date"] == "2025-01-10"].iloc[0]
        n_row = normal[normal["date"] == "2025-01-10"].iloc[0]
        assert cy_row["high_limit"] == pytest.approx(round(cy_row["preclose"] * 1.2, 2))
        assert cy_row["low_limit"] == pytest.approx(round(cy_row["preclose"] * 0.8, 2))
        assert n_row["high_limit"] == pytest.approx(round(n_row["preclose"] * 1.1, 2))
        assert n_row["low_limit"] == pytest.approx(round(n_row["preclose"] * 0.9, 2))

    def test_batch_boundary_split(self, tmp_path):
        """Multi-batch loop must cover every symbol exactly once."""
        out_dir = tmp_path / "stocks"
        out_dir.mkdir()
        # Seed 251 extra symbols to force two batches (batch_size=250)
        rows, metas = [], []
        for i in range(1, 252):
            sym = f"{600000 + i:06d}.SS"
            rows.append(
                f"('{sym}', '2025-01-06', 8.0, 8.5, 8.6, 7.9, 7.8, 100, 850.0)"
            )
            metas.append(f"('{sym}', '1')")
        self.writer.conn.execute(
            "INSERT INTO stock_metadata (symbol, security_type) VALUES "
            + ", ".join(metas)
        )
        self.writer.conn.execute(
            "INSERT INTO stocks (symbol, date, open, close, high, low, preclose, volume, money) "
            "VALUES " + ", ".join(rows)
        )
        self.writer._export_stocks_batch(out_dir, market="cn")
        files = list(out_dir.glob("*.parquet"))
        assert len(files) == 251 + 4
        df = pq.read_table(out_dir / "600005.SS.parquet").to_pandas()
        assert len(df) == 1
        assert df.iloc[0]["close"] == 8.5


@pytest.mark.unit
class TestExportMemoryGuardrail:
    def test_memory_limit_applied_and_reset(self, tmp_path):
        db = tmp_path / "t.duckdb"
        writer = DuckDBWriter(db_path=str(db))
        _seed(writer.conn)
        # Minimal data for the other export stages to run
        writer.conn.execute("INSERT INTO valuation (symbol, date, pe_ttm) "
                            "VALUES ('000001.SZ', '2025-01-06', 10.0)")
        # _export_metadata enforces the benchmark history floor against the
        # stocks-table index row (the preferred export source)
        writer.conn.execute(
            "INSERT INTO benchmark VALUES "
            "('2015-01-01', 1.0, 1.0, 1.0, 1.0, 1.0, 1.0)"
        )
        writer.conn.execute(
            "INSERT INTO stocks (symbol, date, open, close, high, low, "
            "preclose, volume, money) VALUES "
            "('000300.SS', '2015-01-01', 1.0, 1.0, 1.0, 1.0, 1.0, 1, 1.0)"
        )
        try:
            writer.export_to_parquet(str(tmp_path / "out"), market="cn")
            assert writer.conn.execute(
                "SELECT current_setting('memory_limit')"
            ).fetchone()[0] != "2.5GB"
            assert (tmp_path / "tmp_duckdb").exists()
        finally:
            writer.close()
