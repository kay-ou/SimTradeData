"""Anonymous TDX initialization, scoped to an individual SDK API instance."""

from types import MethodType

# Observed V7.71 framing for command 0x000b. The 280-byte decoded body is zero;
# these eight bytes are the repeating wire mask inferred from its padding.
_ANONYMOUS_HEADER = bytes.fromhex("0cbf007b00011a011a010b00")
_ZERO_BODY_MASK = bytes.fromhex("749933ae27700357")


def anonymous_setup_request():
    """Build the validated zero-body request without account/device fields."""
    return _ANONYMOUS_HEADER + _ZERO_BODY_MASK * 35


def install_session(api):
    """Monkeypatch this instance's setup, including automatic reconnects."""
    from tdxpy.parser.setup_commands import BaseSetup

    def setup(instance):
        command = BaseSetup(instance.client, lock=instance.lock)
        command.send_pkg = anonymous_setup_request()
        response = command.call_api()
        if response is None or len(response) <= 2:
            raise ConnectionError("TDX session initialization was rejected")

    api.setup = MethodType(setup, api)
