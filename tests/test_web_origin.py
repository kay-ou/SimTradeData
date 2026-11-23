"""Exercise write authorization through the real HTTP request parser, without sockets."""

from io import BytesIO
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from simtradedata.web.server import handler_for


def post(headers, path="/api/plans", port=8765):
    manager = Mock()
    manager.preview.return_value = {"plan_id": "test"}
    manager.start.return_value = {"job_id": "test"}
    body = b'{"plan_id":"test"}'
    request = (
        f"POST {path} HTTP/1.1\r\n{headers}"
        f"Content-Type: application/json\r\nContent-Length: {len(body)}\r\n\r\n"
    ).encode() + body
    output = BytesIO()
    connection = SimpleNamespace(makefile=lambda *_: BytesIO(request), sendall=output.write)
    handler_for(manager)(connection, ("127.0.0.1", 12345), SimpleNamespace(server_address=("127.0.0.1", port)))
    return int(output.getvalue().split()[1]), manager


@pytest.mark.parametrize("path", ["/api/plans", "/api/jobs", "/api/jobs/test/cancel"])
def test_rebinding_cannot_reach_write_operations(path):
    status, manager = post("Host: attacker.example:8765\r\nOrigin: http://attacker.example:8765\r\nX-SimTrade-UI: 1\r\n", path)
    assert status == 403
    assert not manager.mock_calls


@pytest.mark.parametrize("headers", [
    "Host: localhost:8766\r\nOrigin: http://localhost:8766\r\nX-SimTrade-UI: 1\r\n",
    "Host: localhost:8765\r\nOrigin: http://attacker.example\r\nX-SimTrade-UI: 1\r\n",
    "Host: localhost:8765\r\nOrigin: null\r\nX-SimTrade-UI: 1\r\n",
    "Host: localhost:8765\r\nOrigin: \r\nX-SimTrade-UI: 1\r\n",
    "Host: localhost:8765\r\nHost: attacker.example:8765\r\nX-SimTrade-UI: 1\r\n",
    "Host: localhost:8765\r\nOrigin: http://localhost:8765\r\nOrigin: http://attacker.example\r\nX-SimTrade-UI: 1\r\n",
    "Host: localhost:8765\r\nX-SimTrade-UI: 1\r\nX-SimTrade-UI: 0\r\n",
    "Host: localhost:8765\r\n",
    "X-SimTrade-UI: 1\r\n",
    "Host: localhost:8765@attacker.example\r\nX-SimTrade-UI: 1\r\n",
    "Host: 127.1:8765\r\nX-SimTrade-UI: 1\r\n",
])
def test_invalid_write_headers_are_rejected(headers):
    status, manager = post(headers)
    assert status == 403
    assert not manager.mock_calls


@pytest.mark.parametrize("host,port", [("127.0.0.1:8765", 8765), ("localhost:9123", 9123), ("localhost", 80), ("127.0.0.1:80", 80)])
@pytest.mark.parametrize("with_origin", [True, False])
def test_local_ui_and_cli_remain_allowed(host, port, with_origin):
    headers = f"Host: {host}\r\nX-SimTrade-UI: 1\r\n"
    if with_origin:
        headers += f"Origin: http://{host}\r\n"
    status, manager = post(headers, port=port)
    assert status == 200
    manager.preview.assert_called_once_with({"plan_id": "test"})


@pytest.mark.parametrize("headers,expected", [
    ("Host: attacker.example:8765\r\n", 403),
    ("Host: localhost:8765\r\nHost: attacker.example:8765\r\n", 403),
    ("", 403),
    ("Host: localhost:8765\r\n", 200),
    ("Host: 127.0.0.1:8765\r\n", 200),
])
@pytest.mark.parametrize("path", ["/api/service", "/api/exports/cn/download"])
def test_get_host_boundary(headers, expected, path, tmp_path, monkeypatch):
    manager = Mock(data_dir=tmp_path)
    from simtradedata.web import exports
    package = tmp_path / "cn.zip"
    package.write_bytes(b"local package")
    monkeypatch.setattr(exports, "artifact_path", lambda *a: package)
    raw = f"GET {path} HTTP/1.1\r\n{headers}\r\n".encode()
    output = BytesIO()
    connection = SimpleNamespace(makefile=lambda *_: BytesIO(raw), sendall=output.write)
    handler_for(manager)(connection, ("127.0.0.1", 12345), SimpleNamespace(server_address=("127.0.0.1", 8765)))
    assert int(output.getvalue().split()[1]) == expected
