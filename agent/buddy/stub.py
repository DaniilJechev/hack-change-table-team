"""Process health only: no Telegram, LLM or external MCP calls."""

import json
import logging
import signal
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


class HealthHandler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        if self.path != "/health":
            self.send_error(404)
            return
        body = json.dumps(
            {"status": "ok", "service": "agent", "mode": "stub"}
        ).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format: str, *args: object) -> None:
        logging.debug(format, *args)


def create_server(
    address: tuple[str, int] = ("0.0.0.0", 8081),
) -> ThreadingHTTPServer:
    return ThreadingHTTPServer(address, HealthHandler)


def stop(_signum: int, _frame: object) -> None:
    raise KeyboardInterrupt


def main() -> None:
    logging.basicConfig(level=logging.INFO)
    signal.signal(signal.SIGTERM, stop)
    with create_server() as server:
        logging.info("agent stub started; Telegram and LLM are not connected")
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            logging.info("agent stub stopped")
