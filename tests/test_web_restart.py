import io
import json
from email.message import Message
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from simtradedata.web.jobs import JobManager
from simtradedata.web.server import handler_for


def handler(manager, body=None, origin=None):
    cls = handler_for(manager, restart_enabled=True)
    instance = cls.__new__(cls)
    content = json.dumps(body if body is not None else {}).encode()
    instance.headers = Message()
    for key, value in [('Host', '127.0.0.1:8767'), ('Content-Length', str(len(content))),
                       ('Content-Type', 'application/json'), ('X-SimTrade-UI', '1')]:
        instance.headers[key] = value
    if origin:
        instance.headers['Origin'] = origin
    instance.path = '/api/service/restart'
    instance.rfile = io.BytesIO(content)
    instance.respond = Mock()
    instance.server = SimpleNamespace(server_address=('127.0.0.1', 8767), shutdown=Mock(), restart_requested=False)
    return instance


def test_idle_restart_requests_shutdown_and_blocks_new_jobs(tmp_path):
    manager = JobManager(tmp_path)
    request = handler(manager)
    request.do_POST()
    assert request.respond.call_args.args[0] == 202
    assert manager.restarting and request.server.restart_requested
    request.server.shutdown.assert_called_once()
    with pytest.raises(RuntimeError, match='重启'):
        manager.start('unused-plan')


@pytest.mark.parametrize('status', ['running', 'cancelling'])
def test_active_or_cancelling_task_prevents_restart(tmp_path, status):
    manager = JobManager(tmp_path)
    manager.active = 'current'
    manager.jobs = [{'id': 'current', 'status': status}]
    request = handler(manager)
    request.do_POST()
    assert request.respond.call_args.args[0] == 409
    assert not manager.restarting
    request.server.shutdown.assert_not_called()


@pytest.mark.parametrize('body,origin,expected', [({'command': 'anything'}, None, 400), ({}, 'https://example.org', 403)])
def test_restart_rejects_commands_and_foreign_origins(tmp_path, body, origin, expected):
    request = handler(JobManager(tmp_path), body, origin)
    request.do_POST()
    assert request.respond.call_args.args[0] == expected
    request.server.shutdown.assert_not_called()


def test_duplicate_restart_does_not_schedule_second_shutdown(tmp_path):
    manager = JobManager(tmp_path)
    manager.restarting = True
    request = handler(manager)
    request.do_POST()
    assert request.respond.call_args.args[0] == 409
    request.server.shutdown.assert_not_called()


def test_restart_still_shuts_down_when_browser_disconnects(tmp_path):
    request = handler(JobManager(tmp_path))
    request.respond.side_effect = BrokenPipeError()
    request.do_POST()
    request.server.shutdown.assert_called_once()
    assert request.server.restart_requested
