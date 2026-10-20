"""Tests for import_benchmark_indices (daily-pipeline index import)."""

import struct
import zipfile

import pytest

from scripts.import_tdx_day import (
    RECORD_FORMAT,
    TdxDayImporter,
    import_benchmark_indices,
)


def _day_record(date: int, close_fen: int) -> bytes:
    return struct.pack(RECORD_FORMAT, date, close_fen, close_fen, close_fen,
                       close_fen, 1000.0, 0, 0)


def _make_zip(path, files: dict[str, bytes]) -> None:
    """files: {member_name: bytes}; member must contain '/lday/' and end .day."""
    with zipfile.ZipFile(path, "w") as zf:
        for name, data in files.items():
            zf.writestr(name, data)


_BENCHMARK_FILES = {
    "vipdoc/sh/lday/sh000300.day": (
        _day_record(20260105, 400000) + _day_record(20260106, 401000)
    ),
    "vipdoc/sh/lday/sh000016.day": _day_record(20260105, 100000),
    "vipdoc/sh/lday/sh000905.day": _day_record(20260105, 200000),
    "vipdoc/sh/lday/sh000001.day": _day_record(20260105, 300000),
    "vipdoc/sz/lday/sz399001.day": _day_record(20260105, 500000),
    "vipdoc/sz/lday/sz399006.day": _day_record(20260105, 600000),
}

_EXPECTED_CODES = {
    "000300.SS", "000016.SS", "000905.SS",
    "000001.SS", "399001.SZ", "399006.SZ",
}


@pytest.mark.unit
class TestImportBenchmarkIndices:
    @pytest.fixture()
    def importer(self, tmp_path):
        imp = TdxDayImporter(db_path=str(tmp_path / "t.duckdb"))
        yield imp
        imp.close()

    def test_imports_all_indices_to_stocks_and_benchmark(self, tmp_path, importer):
        zip_path = tmp_path / "hsjday.zip"
        _make_zip(zip_path, _BENCHMARK_FILES)

        imported = import_benchmark_indices(zip_path, importer)

        assert imported == _EXPECTED_CODES
        conn = importer.writer.conn
        # 4 .SS indices (000300 contributes 2 rows) + 2 .SZ indices
        assert conn.execute(
            "SELECT count(*) FROM stocks WHERE symbol LIKE '%.SS'"
        ).fetchone()[0] == 5
        assert conn.execute(
            "SELECT count(*) FROM stocks WHERE symbol LIKE '%.SZ'"
        ).fetchone()[0] == 2
        # Index values are real index points (divisor 100), not fen
        row = conn.execute(
            "SELECT close, volume FROM stocks "
            "WHERE symbol = '000300.SS' ORDER BY date LIMIT 1"
        ).fetchone()
        assert row[0] == pytest.approx(4000.0)
        assert row[1] == 0
        # benchmark table gets the default index only
        assert conn.execute("SELECT count(*) FROM benchmark").fetchone()[0] == 2
        assert conn.execute(
            "SELECT max(close) FROM benchmark"
        ).fetchone()[0] == pytest.approx(4010.0)

    def test_missing_index_file_raises(self, tmp_path, importer):
        zip_path = tmp_path / "hsjday.zip"
        files = dict(_BENCHMARK_FILES)
        del files["vipdoc/sz/lday/sz399006.day"]
        _make_zip(zip_path, files)

        with pytest.raises(ValueError, match="399006.SZ"):
            import_benchmark_indices(zip_path, importer)
