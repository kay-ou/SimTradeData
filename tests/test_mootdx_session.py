import struct
from types import SimpleNamespace

import pytest

from simtradedata.fetchers import mootdx_session as session
from simtradedata.fetchers.mootdx_fetcher import MootdxFetcher


def test_anonymous_request_has_valid_framing_and_no_identity_fields():
    packet = session.anonymous_setup_request()
    size1, size2, command = struct.unpack_from("<HHH", packet, 6)
    assert command == 0x000B
    assert size1 == size2 == len(packet) - 10
    # Decode using the mask independently observed in desktop packet padding.
    mask = bytes.fromhex("749933ae27700357")
    decoded = bytes(value ^ mask[i % 8] for i, value in enumerate(packet[12:]))
    assert decoded == bytes(280)


def test_setup_is_instance_local_and_runs_on_reconnect(monkeypatch):
    from tdxpy.parser.setup_commands import BaseSetup

    calls = []
    monkeypatch.setattr(
        BaseSetup, "call_api",
        lambda command: calls.append((command.client, command.send_pkg)) or bytes(80),
    )
    api = SimpleNamespace(client="first-socket", lock=None)
    other = SimpleNamespace(setup=lambda: "legacy")
    session.install_session(api)
    api.setup()
    api.client = "reconnected-socket"
    api.setup()
    packet = session.anonymous_setup_request()
    assert calls == [("first-socket", packet), ("reconnected-socket", packet)]
    assert other.setup() == "legacy"


@pytest.mark.parametrize("response", [None, b"\x20\x03"])
def test_rejected_initialization_raises(monkeypatch, response):
    from tdxpy.parser.setup_commands import BaseSetup

    monkeypatch.setattr(BaseSetup, "call_api", lambda command: response)
    api = SimpleNamespace(client=object(), lock=None)
    session.install_session(api)
    with pytest.raises(ConnectionError, match="initialization was rejected"):
        api.setup()


def test_fetcher_initializes_before_metadata_and_keeps_setup_on_reconnect(
    monkeypatch,
):
    from mootdx.quotes import Quotes
    from tdxpy.parser.setup_commands import BaseSetup

    requests = []
    connections = []
    monkeypatch.setattr(
        BaseSetup, "call_api",
        lambda command: requests.append(command.send_pkg) or bytes(80),
    )

    class API:
        lock = None
        client = SimpleNamespace(settimeout=lambda timeout: None)

        def connect(self, host, port, **kwargs):
            connections.append((host, port))
            self.setup()
            return True

        def get_security_count(self, market):
            assert requests
            return 5000

    api = API()
    client = SimpleNamespace(client=api, server=("127.0.0.1", 7709), close=lambda: None)
    monkeypatch.setattr(Quotes, "factory", lambda **kwargs: client)
    fetcher = MootdxFetcher()
    fetcher.login()
    assert fetcher._logged_in
    api.connect("127.0.0.1", 7709)
    assert requests == [session.anonymous_setup_request()] * 2
    assert connections == [("127.0.0.1", 7709)] * 2
