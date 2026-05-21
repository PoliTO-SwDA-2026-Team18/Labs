import json
import logging
import os
import socket
from http.server import BaseHTTPRequestHandler, HTTPServer

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger(__name__)

APP_VERSION = os.getenv("APP_VERSION", "1.0.0")
APP_COLOR = os.getenv("APP_COLOR", "blue")
SERVER_PORT = int(os.getenv("SERVER_PORT", 8080))


class ApplicationHTTPServer(BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        logger.debug("%s - %s", self.address_string(), format % args)

    def do_GET(self):
        if self.path == "/":
            self._respond(200, {
                "version": APP_VERSION,
                "color": APP_COLOR,
                "hostname": socket.gethostname()
            })
        elif self.path == "/health":
            self._respond(200, {
                "status": "ok"
            })
        else:
            self._respond(404, {
                "status": "error"
            })

    def _respond(self, status: int, body: dict):
        payload = json.dumps(body).encode()
        self.send_response(status)
        self.send_header('Content-type', 'application/json')
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    @staticmethod
    def run():
        logger.info("Start of server...")
        server_address = ("", SERVER_PORT)
        httpd = HTTPServer(server_address, ApplicationHTTPServer)
        logger.info(f"Listening on :{SERVER_PORT} — version={APP_VERSION} color={APP_COLOR}")
        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            pass
        finally:
            httpd.server_close()
            logger.info("Server down")


if __name__ == "__main__":
    ApplicationHTTPServer.run()
