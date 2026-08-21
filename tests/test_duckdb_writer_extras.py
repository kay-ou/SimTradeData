"""Tests for DuckDB writer extra metadata tables."""

import hashlib
import json
import subprocess
import sys
from pathlib import Path

import pandas as pd
import pyarrow.parquet as pq
import pytest

from simtradedata.fetchers.mootdx_affair_fetcher import MootdxAffairFetcher
from simtradedata.writers.duckdb_writer import DuckDBWriter


def _seed_benchmark(conn):
    conn.execute(
        "INSERT INTO benchmark VALUES ('2015-01-01', 1.0, 1.0, 1.0, 1.0, 1.0, 1.0)"
    )


@pytest.mark.unit
class TestWriteMoneyFlow:
    def setup_method(self):
        self.writer = DuckDBWriter(db_path=":memory:")

    def teardown_method(self):
        self.writer.close()

    def test_write_and_read(self):
        df = pd.DataFrame({
            "date": pd.to_datetime(["2025-01-02"]),
            "net_main": [1000.0], "net_super": [-500.0],
            "net_large": [1500.0], "net_medium": [-800.0], "net_small": [-200.0],
        })
        self.writer.write_money_flow("000001.SZ", df)
        result = self.writer.conn.execute(
            "SELECT * FROM money_flow WHERE symbol = '000001.SZ'"
        ).fetchdf()
        assert len(result) == 1
        assert result.iloc[0]["net_main"] == pytest.approx(1000.0)

    def test_upsert_replaces(self):
        df1 = pd.DataFrame({
            "date": pd.to_datetime(["2025-01-02"]),
            "net_main": [1000.0], "net_super": [0.0],
            "net_large": [0.0], "net_medium": [0.0], "net_small": [0.0],
        })
        df2 = pd.DataFrame({
            "date": pd.to_datetime(["2025-01-02"]),
            "net_main": [2000.0], "net_super": [0.0],
            "net_large": [0.0], "net_medium": [0.0], "net_small": [0.0],
        })
        self.writer.write_money_flow("000001.SZ", df1)
        self.writer.write_money_flow("000001.SZ", df2)
        result = self.writer.conn.execute(
            "SELECT net_main FROM money_flow WHERE symbol = '000001.SZ'"
        ).fetchone()
        assert result[0] == pytest.approx(2000.0)


@pytest.mark.unit
class TestExpandedFundamentals:
    def setup_method(self):
        self.writer = DuckDBWriter(db_path=":memory:")

    def teardown_method(self):
        self.writer.close()

    def test_mootdx_affair_default_keeps_statement_fields(self):
        raw = pd.DataFrame([[0.0] * 315], index=["000001"])
        raw.iloc[0, 0] = 260331
        raw.iloc[0, 40] = 123.0
        raw.iloc[0, 74] = 456.0
        raw.iloc[0, 107] = 789.0
        raw.iloc[0, 314] = 260430

        result = MootdxAffairFetcher()._convert_to_ptrade_format(raw)

        assert result.loc[0, "end_date"] == pd.Timestamp("2026-03-31")
        assert result.loc[0, "publ_date"] == pd.Timestamp("2026-04-30")
        assert result.loc[0, "total_assets"] == pytest.approx(123.0)
        assert result.loc[0, "operating_revenue"] == pytest.approx(456.0)
        assert result.loc[0, "net_operate_cash_flow"] == pytest.approx(789.0)

    def test_write_and_export_expanded_fundamentals(self, tmp_path):
        df = pd.DataFrame({
            "date": pd.to_datetime(["2026-03-31"]),
            "publ_date": pd.to_datetime(["2026-04-30"]),
            "roe": [10.5],
            "total_assets": [123.0],
            "total_liability": [45.0],
            "operating_revenue": [456.0],
            "net_profit": [67.0],
            "net_operate_cash_flow": [789.0],
            "total_shares": [1000.0],
            "a_floats": [800.0],
        })

        self.writer.write_fundamentals("000001.SZ", df)

        stored = self.writer.conn.execute("""
            SELECT total_assets, operating_revenue, net_operate_cash_flow
            FROM fundamentals
            WHERE symbol = '000001.SZ'
        """).fetchone()
        assert stored == pytest.approx((123.0, 456.0, 789.0))

        self.writer.write_stock_metadata(pd.DataFrame({
            "symbol": ["000001.SZ"],
            "stock_name": ["Ping An Bank"],
            "listed_date": ["1991-04-03"],
            "de_listed_date": ["2900-01-01"],
            "security_type": ["1"],
            "listing_status": ["1"],
            "blocks": ["{}"],
        }))
        _seed_benchmark(self.writer.conn)
        self.writer.export_to_parquet(str(tmp_path), market="cn")

        exported = pq.read_table(
            tmp_path / "fundamentals" / "000001.SZ.parquet"
        ).to_pandas()
        manifest = json.loads((tmp_path / "manifest.json").read_text(encoding="utf-8"))
        assert manifest["package_format"] == "simtrade-data-market-v1"
        assert manifest["mode"] == "full"
        assert "total_assets" in exported.columns
        assert "operating_revenue" in exported.columns
        assert "net_operate_cash_flow" in exported.columns
        assert exported.loc[0, "total_assets"] == pytest.approx(123.0)


@pytest.mark.unit
class TestWriteLhb:
    def setup_method(self):
        self.writer = DuckDBWriter(db_path=":memory:")

    def teardown_method(self):
        self.writer.close()

    def test_write_and_read(self):
        df = pd.DataFrame({
            "symbol": ["000001.SZ"],
            "date": pd.to_datetime(["2025-01-02"]),
            "reason": ["daily_limit"],
            "net_buy": [5e7], "buy_amount": [8e7], "sell_amount": [3e7],
        })
        self.writer.write_lhb(df)
        result = self.writer.conn.execute("SELECT * FROM lhb").fetchdf()
        assert len(result) == 1


@pytest.mark.unit
class TestWriteMarginTrading:
    def setup_method(self):
        self.writer = DuckDBWriter(db_path=":memory:")

    def teardown_method(self):
        self.writer.close()

    def test_write_and_read(self):
        df = pd.DataFrame({
            "date": pd.to_datetime(["2025-01-02"]),
            "rzye": [1e9], "rqyl": [5e7], "rzrqye": [1.05e9],
        })
        self.writer.write_margin_trading("000001.SZ", df)
        result = self.writer.conn.execute(
            "SELECT * FROM margin_trading WHERE symbol = '000001.SZ'"
        ).fetchdf()
        assert len(result) == 1


@pytest.mark.unit
class TestWriteIndexConstituents:
    def setup_method(self):
        self.writer = DuckDBWriter(db_path=":memory:")

    def teardown_method(self):
        self.writer.close()

    def test_write_and_replace(self):
        self.writer.write_index_constituents(
            "20260519", "000300.SS", ["600000.SS", "000001.SZ"]
        )
        self.writer.write_index_constituents(
            "20260519", "000300.SS", ["600000.SS", "600519.SS"]
        )

        result = self.writer.conn.execute(
            """
            SELECT date, index_code, symbols
            FROM index_constituents
            WHERE date = '20260519' AND index_code = '000300.SS'
            """
        ).fetchone()

        assert result[0] == "20260519"
        assert result[1] == "000300.SS"
        assert json.loads(result[2]) == ["600000.SS", "600519.SS"]


@pytest.mark.unit
class TestExportDelta:
    def setup_method(self):
        self.writer = DuckDBWriter(db_path=":memory:")

    def teardown_method(self):
        self.writer.close()

    def _seed_delta_data(self):
        market_df = pd.DataFrame({
            "date": pd.to_datetime(["2026-06-19", "2026-06-22"]),
            "open": [10.0, 10.5],
            "close": [10.2, 10.8],
            "high": [10.3, 10.9],
            "low": [9.9, 10.4],
            "preclose": [9.8, 10.2],
            "volume": [1000, 1200],
            "money": [10000.0, 12960.0],
        })
        valuation_df = pd.DataFrame({
            "date": pd.to_datetime(["2026-06-22"]),
            "pe_ttm": [12.3],
            "pb": [1.4],
        })
        fundamentals_df = pd.DataFrame({
            "date": pd.to_datetime(["2026-03-31", "2026-06-22"]),
            "publ_date": pd.to_datetime(["2026-04-20", "2026-06-22"]),
            "roe": [9.1, 9.8],
            "total_shares": [100.0, 100.0],
            "a_floats": [80.0, 80.0],
        })
        exrights_df = pd.DataFrame({
            "date": pd.to_datetime(["2026-06-22"]),
            "allotted_ps": [0.0],
            "rationed_ps": [0.0],
            "rationed_px": [0.0],
            "bonus_ps": [0.1],
            "dividend": [0.2],
        })
        benchmark_df = pd.DataFrame({
            "date": pd.to_datetime(["2015-01-01", "2026-06-22"]),
            "open": [3000.0, 4000.0],
            "high": [3010.0, 4010.0],
            "low": [2990.0, 3990.0],
            "close": [3005.0, 4005.0],
            "volume": [50000.0, 100000.0],
            "money": [1e9, 2e9],
        })

        self.writer.write_market_data("000001.SZ", market_df)
        self.writer.write_market_data("430001.BJ", market_df)
        self.writer.write_valuation("000001.SZ", valuation_df)
        self.writer.write_fundamentals("000001.SZ", fundamentals_df)
        self.writer.write_exrights("000001.SZ", exrights_df)
        self.writer.write_benchmark(benchmark_df)
        self.writer.write_trade_days(pd.DataFrame({"date": pd.to_datetime(["2026-06-22"])}))
        self.writer.write_index_constituents("20260622", "000300.SS", ["000001.SZ"])
        self.writer.write_stock_status("20260622", "HALT", ["000001.SZ"])
        self.writer.write_stock_metadata(pd.DataFrame({
            "symbol": ["000001.SZ", "430001.BJ"],
            "stock_name": ["Ping An Bank", "Filtered BJ"],
            "listed_date": ["1991-04-03", "2022-01-01"],
            "de_listed_date": ["2900-01-01", "2900-01-01"],
            "security_type": ["1", "1"],
            "listing_status": ["1", "1"],
            "blocks": ["{}", "{}"],
        }))

    def test_export_delta_writes_changed_rows_and_manifest(self, tmp_path):
        self._seed_delta_data()
        self.writer.conn.execute("DELETE FROM data_change_log")

        self.writer.export_delta(
            str(tmp_path),
            base_version="2026-06-20",
            target_version="2026-06-22",
            market="cn",
        )

        manifest = json.loads((tmp_path / "manifest.json").read_text(encoding="utf-8"))
        assert manifest["package_format"] == "simtradedata_delta_v1"
        assert manifest["base_version"] == "2026-06-20"
        assert manifest["target_version"] == "2026-06-22"
        assert manifest["changed_symbols"] == ["000001.SZ"]
        assert {item["table"] for item in manifest["changed_tables"]} == {
            "stocks",
            "valuation",
            "fundamentals",
            "exrights",
            "trade_days",
            "benchmark",
            "index_constituents",
            "stock_status",
            "stock_metadata",
        }
        paths = {item["path"] for item in manifest["files"]}
        assert paths == {
            "stocks/000001.SZ.parquet",
            "valuation/000001.SZ.parquet",
            "fundamentals/000001.SZ.parquet",
            "exrights/000001.SZ.parquet",
            "metadata/trade_days.parquet",
            "metadata/benchmark.parquet",
            "metadata/index_constituents.parquet",
            "metadata/stock_status.parquet",
            "metadata/stock_metadata.parquet",
            "metadata/version.parquet",
        }
        for item in manifest["files"]:
            file_path = tmp_path / item["path"]
            assert item["sha256"] == hashlib.sha256(file_path.read_bytes()).hexdigest()
            assert item["size"] == file_path.stat().st_size

        stocks = pd.read_parquet(tmp_path / "stocks" / "000001.SZ.parquet")
        assert stocks["date"].dt.strftime("%Y-%m-%d").tolist() == ["2026-06-22"]
        assert stocks.iloc[0]["close"] == pytest.approx(10.8)
        assert "stocks/430001.BJ.parquet" not in paths

    def test_export_delta_uses_latest_stock_date_as_default_target(self, tmp_path):
        self._seed_delta_data()

        self.writer.export_delta(str(tmp_path), base_version="2026-06-20", market="cn")

        manifest = json.loads((tmp_path / "manifest.json").read_text(encoding="utf-8"))
        assert manifest["target_version"] == "2026-06-22"

    def test_export_delta_rejects_empty_delta(self, tmp_path):
        market_df = pd.DataFrame({
            "date": pd.to_datetime(["2026-06-19"]),
            "open": [10.0],
            "close": [10.2],
            "high": [10.3],
            "low": [9.9],
            "preclose": [9.8],
            "volume": [1000],
            "money": [10000.0],
        })
        self.writer.write_market_data("000001.SZ", market_df)
        self.writer.conn.execute("DELETE FROM data_change_log")

        with pytest.raises(ValueError, match="no changed rows"):
            self.writer.export_delta(
                str(tmp_path),
                base_version="2026-06-20",
                target_version="2026-06-22",
                market="cn",
            )
        assert not tmp_path.exists()

    def test_export_delta_symbol_tables_match_full_export_rows(self, tmp_path):
        self._seed_delta_data()
        self.writer.conn.execute("DELETE FROM data_change_log")
        full_dir = tmp_path / "full"
        delta_dir = tmp_path / "delta"

        self.writer.export_delta(
            str(delta_dir),
            base_version="2026-06-20",
            target_version="2026-06-22",
            market="cn",
        )
        self.writer.export_to_parquet(str(full_dir), market="cn")

        for table in ["stocks", "valuation", "fundamentals", "exrights"]:
            delta_rows = pd.read_parquet(delta_dir / table / "000001.SZ.parquet")
            full_rows = pd.read_parquet(full_dir / table / "000001.SZ.parquet")
            expected_rows = full_rows[
                (full_rows["date"] > pd.Timestamp("2026-06-20"))
                & (full_rows["date"] <= pd.Timestamp("2026-06-22"))
            ]

            pd.testing.assert_frame_equal(
                delta_rows.reset_index(drop=True),
                expected_rows.reset_index(drop=True),
                check_dtype=False,
            )

    def test_full_export_includes_us_symbol_tables(self, tmp_path):
        market_df = pd.DataFrame({
            "date": pd.to_datetime(["2026-06-22"]),
            "open": [200.0],
            "close": [202.0],
            "high": [203.0],
            "low": [199.0],
            "preclose": [198.0],
            "volume": [1000000],
            "money": [202000000.0],
        })
        self.writer.write_market_data("AAPL.US", market_df)
        self.writer.write_valuation(
            "AAPL.US",
            pd.DataFrame({
                "date": pd.to_datetime(["2026-06-22"]),
                "pe_ttm": [25.0],
                "pb": [8.0],
            }),
        )
        self.writer.write_fundamentals(
            "AAPL.US",
            pd.DataFrame({
                "date": pd.to_datetime(["2026-03-31"]),
                "roe": [20.0],
                "total_shares": [100.0],
                "a_floats": [90.0],
            }),
        )
        self.writer.write_exrights(
            "AAPL.US",
            pd.DataFrame({
                "date": pd.to_datetime(["2026-06-22"]),
                "allotted_ps": [0.0],
                "rationed_ps": [0.0],
                "rationed_px": [0.0],
                "bonus_ps": [0.0],
                "dividend": [0.25],
            }),
        )

        self.writer.export_to_parquet(str(tmp_path), market="us")

        assert (tmp_path / "stocks" / "AAPL.US.parquet").exists()
        assert (tmp_path / "valuation" / "AAPL.US.parquet").exists()
        assert (tmp_path / "fundamentals" / "AAPL.US.parquet").exists()
        assert (tmp_path / "exrights" / "AAPL.US.parquet").exists()

    def test_full_export_skips_valuation_symbol_without_stock_match(self, tmp_path):
        market_df = pd.DataFrame({
            "date": pd.to_datetime(["2026-06-22"]),
            "open": [10.0],
            "close": [10.5],
            "high": [10.8],
            "low": [9.9],
            "preclose": [9.8],
            "volume": [1000],
            "money": [10500.0],
        })
        valuation_df = pd.DataFrame({
            "date": pd.to_datetime(["2026-06-22"]),
            "pe_ttm": [12.0],
            "pb": [1.5],
        })

        self.writer.write_market_data("000001.SZ", market_df)
        self.writer.write_valuation("000001.SZ", valuation_df)
        self.writer.write_valuation("000002.SZ", valuation_df)
        _seed_benchmark(self.writer.conn)

        self.writer.export_to_parquet(str(tmp_path), market="cn")

        assert (tmp_path / "valuation" / "000001.SZ.parquet").exists()
        assert not (tmp_path / "valuation" / "000002.SZ.parquet").exists()

    def test_export_delta_exrights_can_rebuild_changed_factors(self, tmp_path):
        base_dir = tmp_path / "base" / "exrights"
        target_dir = tmp_path / "target" / "exrights"
        base_dir.mkdir(parents=True)
        target_dir.mkdir(parents=True)

        self.writer.write_exrights("000001.SZ", pd.DataFrame({
            "date": pd.to_datetime(["2026-06-19"]),
            "allotted_ps": [0.0],
            "rationed_ps": [0.0],
            "rationed_px": [0.0],
            "bonus_ps": [0.1],
            "dividend": [0.2],
        }))
        self.writer._export_exrights_batch(base_dir)

        self.writer.write_exrights("000001.SZ", pd.DataFrame({
            "date": pd.to_datetime(["2026-06-22"]),
            "allotted_ps": [0.1],
            "rationed_ps": [0.0],
            "rationed_px": [0.0],
            "bonus_ps": [0.2],
            "dividend": [0.3],
        }))
        self.writer.export_delta(
            str(tmp_path / "delta"),
            base_version="2026-06-20",
            target_version="2026-06-22",
            market="cn",
        )
        self.writer._export_exrights_batch(target_dir)

        base_rows = pd.read_parquet(base_dir / "000001.SZ.parquet")
        delta_rows = pd.read_parquet(tmp_path / "delta" / "exrights" / "000001.SZ.parquet")
        expected = pd.read_parquet(target_dir / "000001.SZ.parquet")

        assert delta_rows["date"].dt.strftime("%Y-%m-%d").tolist() == [
            "2026-06-19",
            "2026-06-22",
        ]
        rebuilt = pd.concat([base_rows, delta_rows], ignore_index=True)
        rebuilt = rebuilt.drop_duplicates(["date"], keep="last").sort_values("date")
        pd.testing.assert_frame_equal(
            rebuilt.reset_index(drop=True),
            expected.reset_index(drop=True),
            check_dtype=False,
        )

    def test_export_delta_enriches_halt_status_from_zero_volume(self, tmp_path):
        market_df = pd.DataFrame({
            "date": pd.to_datetime(["2026-06-19", "2026-06-22"]),
            "open": [10.0, 10.0],
            "close": [10.2, 10.2],
            "high": [10.3, 10.2],
            "low": [9.9, 10.2],
            "preclose": [9.8, 10.2],
            "volume": [1000, 0],
            "money": [10000.0, 0.0],
        })
        self.writer.write_market_data("000001.SZ", market_df)

        self.writer.export_delta(
            str(tmp_path),
            base_version="2026-06-20",
            target_version="2026-06-22",
            market="cn",
        )

        status = pd.read_parquet(tmp_path / "metadata" / "stock_status.parquet")
        assert status["date"].tolist() == ["20260622"]
        assert status.iloc[0]["status_type"] == "HALT"
        assert list(status.iloc[0]["symbols"]) == ["000001.SZ"]

    def test_delta_rows_can_rebuild_symbol_file(self, tmp_path):
        self._seed_delta_data()
        full_dir = tmp_path / "full"
        delta_dir = tmp_path / "delta"

        self.writer.export_to_parquet(str(full_dir), market="cn")
        full_rows = pd.read_parquet(full_dir / "stocks" / "000001.SZ.parquet")
        base_rows = full_rows[full_rows["date"] <= pd.Timestamp("2026-06-20")]

        self.writer.export_delta(
            str(delta_dir),
            base_version="2026-06-20",
            target_version="2026-06-22",
            market="cn",
        )

        delta_rows = pd.read_parquet(delta_dir / "stocks" / "000001.SZ.parquet")
        rebuilt = pd.concat([base_rows, delta_rows], ignore_index=True)
        rebuilt = rebuilt.drop_duplicates(["date"], keep="last").sort_values("date")
        expected = full_rows[full_rows["date"] <= pd.Timestamp("2026-06-22")]

        pd.testing.assert_frame_equal(
            rebuilt.reset_index(drop=True),
            expected.reset_index(drop=True),
            check_dtype=False,
        )

    def test_export_delta_includes_historical_correction(self, tmp_path):
        market_df = pd.DataFrame({
            "date": pd.to_datetime(["2026-06-19", "2026-06-22"]),
            "open": [10.0, 10.5],
            "close": [10.2, 10.8],
            "high": [10.3, 10.9],
            "low": [9.9, 10.4],
            "preclose": [9.8, 10.2],
            "volume": [1000, 1200],
            "money": [10000.0, 12960.0],
        })
        self.writer.write_market_data("000001.SZ", market_df)
        self.writer.conn.execute("DELETE FROM data_change_log")

        corrected = market_df.copy()
        corrected.loc[0, "close"] = 10.4
        self.writer.write_market_data("000001.SZ", corrected.iloc[[0]])

        self.writer.export_delta(
            str(tmp_path),
            base_version="2026-06-20",
            target_version="2026-06-22",
            market="cn",
        )

        delta_rows = pd.read_parquet(tmp_path / "stocks" / "000001.SZ.parquet")
        assert delta_rows["date"].dt.strftime("%Y-%m-%d").tolist() == [
            "2026-06-19",
            "2026-06-22",
        ]
        assert delta_rows.iloc[0]["close"] == pytest.approx(10.4)

    def test_export_delta_includes_metadata_only_change(self, tmp_path):
        market_df = pd.DataFrame({
            "date": pd.to_datetime(["2026-06-19"]),
            "open": [10.0],
            "close": [10.2],
            "high": [10.3],
            "low": [9.9],
            "preclose": [9.8],
            "volume": [1000],
            "money": [10000.0],
        })
        self.writer.write_market_data("000001.SZ", market_df)
        self.writer.conn.execute("DELETE FROM data_change_log")
        self.writer.write_stock_metadata(pd.DataFrame({
            "symbol": ["000001.SZ"],
            "stock_name": ["Updated Name"],
            "listed_date": ["1991-04-03"],
            "de_listed_date": ["2900-01-01"],
            "blocks": ['{"ZJHHY": ["Bank"]}'],
        }))

        self.writer.export_delta(
            str(tmp_path),
            base_version="2026-06-20",
            target_version="2026-06-22",
            market="cn",
        )

        manifest = json.loads((tmp_path / "manifest.json").read_text(encoding="utf-8"))
        assert manifest["changed_symbols"] == ["000001.SZ"]
        assert {item["table"] for item in manifest["changed_tables"]} == {"stock_metadata"}
        metadata = pd.read_parquet(tmp_path / "metadata" / "stock_metadata.parquet")
        assert metadata.iloc[0]["stock_name"] == "Updated Name"

    def test_export_delta_official_security_type_overrides_prefix_fallback(self, tmp_path):
        market_df = pd.DataFrame({
            "date": pd.to_datetime(["2026-06-22"]),
            "open": [10.0],
            "close": [10.2],
            "high": [10.3],
            "low": [9.9],
            "preclose": [9.8],
            "volume": [1000],
            "money": [10000.0],
        })
        self.writer.write_market_data("002999.SZ", market_df)
        self.writer.write_stock_metadata(pd.DataFrame({
            "symbol": ["002999.SZ"],
            "stock_name": ["ETF-like security"],
            "listed_date": ["2026-01-01"],
            "de_listed_date": ["2900-01-01"],
            "security_type": ["5"],
            "listing_status": ["1"],
            "blocks": ["{}"],
        }))

        with pytest.raises(ValueError, match="no changed rows"):
            self.writer.export_delta(
                str(tmp_path),
                base_version="2026-06-20",
                target_version="2026-06-22",
                market="cn",
            )

    def test_export_delta_requires_metadata_when_security_type_catalog_exists(self, tmp_path):
        market_df = pd.DataFrame({
            "date": pd.to_datetime(["2026-06-22"]),
            "open": [10.0],
            "close": [10.2],
            "high": [10.3],
            "low": [9.9],
            "preclose": [9.8],
            "volume": [1000],
            "money": [10000.0],
        })
        self.writer.write_market_data("000001.SZ", market_df)
        self.writer.write_market_data("002999.SZ", market_df)
        self.writer.write_stock_metadata(pd.DataFrame({
            "symbol": ["000001.SZ"],
            "stock_name": ["Ping An Bank"],
            "listed_date": ["1991-04-03"],
            "de_listed_date": ["2900-01-01"],
            "security_type": ["1"],
            "listing_status": ["1"],
            "blocks": ["{}"],
        }))

        self.writer.export_delta(
            str(tmp_path),
            base_version="2026-06-20",
            target_version="2026-06-22",
            market="cn",
        )

        manifest = json.loads((tmp_path / "manifest.json").read_text(encoding="utf-8"))
        assert manifest["changed_symbols"] == ["000001.SZ"]
        assert (tmp_path / "stocks" / "000001.SZ.parquet").exists()
        assert not (tmp_path / "stocks" / "002999.SZ.parquet").exists()

    def test_cli_missing_db_exits_nonzero(self, tmp_path):
        result = subprocess.run(
            [
                sys.executable,
                "scripts/export_parquet.py",
                "--db",
                str(tmp_path / "missing.duckdb"),
                "--delta",
                "--base-version",
                "2026-06-20",
            ],
            cwd=Path(__file__).resolve().parents[1],
            capture_output=True,
            text=True,
            check=False,
        )

        assert result.returncode != 0
        assert "Database not found" in result.stderr
