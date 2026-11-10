import json
import signal
from threading import Thread
from urllib.error import HTTPError
from urllib.request import urlopen

import pytest

from buddy import stub


@pytest.fixture
def server():
    with stub.create_server(("127.0.0.1", 0)) as service:
        thread = Thread(target=service.serve_forever, daemon=True)
        thread.start()
        try:
            yield service
        finally:
            service.shutdown()
            thread.join(timeout=2)
            assert not thread.is_alive()


def test_nfr_health_1_http(server):
    host, port = server.server_address
    with urlopen(f"http://{host}:{port}/health", timeout=2) as response:
        assert response.status == 200
        assert response.headers.get_content_type() == "application/json"
        assert json.load(response) == {
            "status": "ok", "service": "agent", "mode": "stub"
        }


@pytest.mark.parametrize("path", ["/missing", "/health/other"])
def test_nfr_health_1_unknown_path(server, path):
    host, port = server.server_address
    with pytest.raises(HTTPError) as error:
        urlopen(f"http://{host}:{port}{path}", timeout=2)
    assert error.value.code == 404
    error.value.close()


def test_nfr_boot_1_shutdown_closes_listener(monkeypatch):
    service = stub.create_server(("127.0.0.1", 0))

    def interrupt():
        raise KeyboardInterrupt

    monkeypatch.setattr(stub, "create_server", lambda: service)
    monkeypatch.setattr(service, "serve_forever", interrupt)
    previous_handler = signal.getsignal(signal.SIGTERM)
    try:
        stub.main()
    finally:
        signal.signal(signal.SIGTERM, previous_handler)
    assert service.fileno() == -1


def test_nfr_boot_1_sigterm():
    with pytest.raises(KeyboardInterrupt):
        stub.stop(signal.SIGTERM, None)
