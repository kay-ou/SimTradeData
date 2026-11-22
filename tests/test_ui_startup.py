"""Verify automatic port selection without touching running local services."""
import errno
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from simtradedata.web import server


def setup_service(monkeypatch, occupied):
    manager = SimpleNamespace(data_dir=Path('data'), active=None)
    health = SimpleNamespace(start=Mock(), stop=Mock())
    listener = SimpleNamespace(server_port=8767, serve_forever=Mock(), server_close=Mock())
    attempted = []

    def bind(address, handler):
        attempted.append(address)
        if address[1] in occupied:
            raise OSError(errno.EADDRINUSE, 'Occupied')
        listener.server_port = address[1]
        return listener

    monkeypatch.setattr(server, 'JobManager', lambda directory: manager)
    monkeypatch.setattr(server, 'SourceHealth', lambda directory: health)
    monkeypatch.setattr(server, 'handler_for', lambda *args, **kwargs: object())
    monkeypatch.setattr(server, 'ThreadingHTTPServer', bind)
    return attempted, health, listener


def test_default_start_skips_occupied_ports(monkeypatch, capsys):
    attempted, health, listener = setup_service(monkeypatch, {8765, 8766})
    server.main([])
    assert attempted == [('127.0.0.1', 8765), ('127.0.0.1', 8766), ('127.0.0.1', 8767)]
    assert 'http://127.0.0.1:8767' in capsys.readouterr().out
    health.start.assert_called_once()
    listener.server_close.assert_called_once()


def test_explicit_occupied_port_is_not_silently_changed(monkeypatch):
    attempted, health, _ = setup_service(monkeypatch, {8765})
    with pytest.raises(SystemExit) as error:
        server.main(['--port', '8765'])
    assert error.value.code == 2
    assert attempted == [('127.0.0.1', 8765)]
    health.start.assert_not_called()


def test_no_available_default_port_fails_boundedly(monkeypatch):
    attempted, health, _ = setup_service(monkeypatch, set(range(8765, 8785)))
    with pytest.raises(SystemExit):
        server.main([])
    assert len(attempted) == 20
    health.start.assert_not_called()


def test_restart_pins_automatically_selected_port(monkeypatch):
    occupied = {8765}
    attempted, _, listener = setup_service(monkeypatch, occupied)
    listener.serve_forever.side_effect = lambda: setattr(listener, 'restart_requested', True)
    monkeypatch.setattr(server.sys, 'orig_argv', ['python', '-m', 'simtradedata.web', '--data-dir', '/tmp/test data'])
    execute = Mock()
    monkeypatch.setattr(server.os, 'execv', execute)
    server.main([])
    arguments = execute.call_args.args[1]
    assert arguments[-2:] == ['--port', '8766']
    assert arguments[1:5] == ['-m', 'simtradedata.web', '--data-dir', '/tmp/test data']
    occupied.clear()
    listener.serve_forever.side_effect = None
    server.main(arguments[3:])
    assert attempted[-1] == ('127.0.0.1', 8766)
