from scripts import download_tdx_day


class FakeResponse:
    def __init__(self):
        self.headers = {"Content-Length": "5"}
        self._chunks = [b"abc", b""]

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def read(self, _size):
        return self._chunks.pop(0)


def test_download_file_size_mismatch_reports_and_cleans_temp(tmp_path, monkeypatch):
    monkeypatch.setattr(download_tdx_day, "urlopen", lambda *_args, **_kwargs: FakeResponse())

    dest_path = tmp_path / "hsjday.zip"

    assert download_tdx_day.download_file(
        "https://example.test/hsjday.zip",
        dest_path,
        show_progress=False,
    ) is False
    assert not dest_path.exists()
    assert not dest_path.with_suffix(".tmp").exists()


def test_download_callback_reports_actual_transferred_bytes(tmp_path, monkeypatch):
    response = FakeResponse()
    response.headers = {"Content-Length": "5"}
    response._chunks = [b"abc", b"de", b""]
    monkeypatch.setattr(download_tdx_day, "urlopen", lambda *args, **kwargs: response)
    events = []
    target = tmp_path / "hsjday.zip"
    assert download_tdx_day.download_file("https://example.test/hsjday.zip", target, False,
                                         progress_callback=lambda done, total: events.append((done, total)))
    assert target.read_bytes() == b"abcde"
    assert events == [(0, 5), (3, 5), (5, 5)]
