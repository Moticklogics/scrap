"""Tiny HTTP server that serves the fixture forums (tests/fixtures/sites/routes.json)."""
from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

SITES = Path(__file__).parent / "sites"


class FixtureServer:
    def __init__(self, host: str = "127.0.0.1", port: int = 0):
        self.routes = json.loads((SITES / "routes.json").read_text(encoding="utf-8"))
        self.fail_counts: dict[str, int] = {}
        self.hits: dict[str, int] = {}
        self.lock = threading.Lock()
        server = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):  # silence
                pass

            def do_GET(self):
                path = self.path
                with server.lock:
                    server.hits[path] = server.hits.get(path, 0) + 1
                route = server.routes.get(path)
                if route is None:
                    self.send_response(404)
                    self.send_header("Content-Type", "text/html; charset=utf-8")
                    self.end_headers()
                    self.wfile.write(b"<html><body><h1>404 Not Found</h1></body></html>")
                    return
                fail_times = route.get("fail_times", 0)
                with server.lock:
                    n = server.fail_counts.get(path, 0)
                    if n < fail_times:
                        server.fail_counts[path] = n + 1
                        failing = True
                    else:
                        failing = False
                if failing:
                    self.send_response(503)
                    self.send_header("Content-Type", "text/html")
                    self.end_headers()
                    self.wfile.write(b"<html><body>Service temporarily unavailable</body></html>")
                    return
                data = (SITES / route["file"]).read_bytes()
                self.send_response(route.get("status", 200))
                self.send_header("Content-Type", route.get("content_type", "text/html") + "; charset=utf-8")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

        self.httpd = ThreadingHTTPServer((host, port), Handler)
        self.httpd.daemon_threads = True
        self.port = self.httpd.server_address[1]
        self.base = f"http://{host}:{self.port}"
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)

    def start(self) -> "FixtureServer":
        self.thread.start()
        return self

    def stop(self) -> None:
        self.httpd.shutdown()
        self.httpd.server_close()

    def reset(self) -> None:
        with self.lock:
            self.fail_counts.clear()
            self.hits.clear()


if __name__ == "__main__":
    import sys
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8800
    s = FixtureServer(port=port).start()
    print(f"serving fixtures on {s.base}")
    try:
        s.thread.join()
    except KeyboardInterrupt:
        s.stop()
