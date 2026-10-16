"""The runtime image has no httpx (dev-only): production RPC paths must not import it."""

from __future__ import annotations

import pathlib

ROOT = pathlib.Path(__file__).resolve().parents[1]
RUNTIME_DIRS = ("core", "api")


def test_runtime_code_does_not_import_httpx() -> None:
    offenders = []
    for d in RUNTIME_DIRS:
        for path in (ROOT / d).rglob("*.py"):
            text = path.read_text(encoding="utf-8")
            if "import httpx" in text or "from httpx" in text:
                offenders.append(str(path.relative_to(ROOT)))
    assert offenders == [], f"httpx is a dev dependency; offenders: {offenders}"


def test_http_rpc_keep_alive_and_retry_on_closed_connection() -> None:
    """http_rpc reuses connections with keep-alive and retries once on closed connection."""
    import http.server
    import json
    import threading
    from typing import Any

    from api.main import http_rpc

    class ServerState:
        drop_next = False
        request_count = 0
        connection_ports: list[int] = []

    state = ServerState()

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_POST(self) -> None:
            state.request_count += 1
            state.connection_ports.append(self.client_address[1])

            if state.drop_next:
                state.drop_next = False
                # Close connection abruptly without response to simulate closed keep-alive socket
                self.close_connection = True
                return

            content_length = int(self.headers.get("Content-Length", 0))
            self.rfile.read(content_length)

            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Connection", "keep-alive")
            body = json.dumps(
                {"jsonrpc": "2.0", "id": 1, "result": f"res_{state.request_count}"}
            ).encode()
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, format: str, *args: Any) -> None:
            pass  # Suppress test output noise

    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    server_thread = threading.Thread(target=server.serve_forever, daemon=True)
    server_thread.start()

    try:
        host, port = server.server_address
        rpc = http_rpc(f"http://{host}:{port}")

        # 1. First call initializes connection
        r1 = rpc("method1", [])
        assert r1 == "res_1"

        # 2. Second call reuses the same connection (same client port)
        r2 = rpc("method2", [])
        assert r2 == "res_2"
        assert state.connection_ports[0] == state.connection_ports[1]

        # 3. Simulate server dropping the connection on next request
        state.drop_next = True

        # 4. Third call encounters closed socket on reuse, retries automatically, and succeeds!
        r3 = rpc("method3", [])
        assert r3 == "res_4"  # res_3 was dropped, res_4 is the retried success
        # The retried call established a new connection with a new client port
        assert state.connection_ports[3] != state.connection_ports[0]
    finally:
        server.shutdown()
        server.server_close()


def test_http_rpc_error_handling() -> None:
    """http_rpc raises RuntimeError on RPC errors or non-200 HTTP responses."""
    import http.server
    import json
    import threading
    from typing import Any

    import pytest

    from api.main import http_rpc

    class ErrorHandler(http.server.BaseHTTPRequestHandler):
        def do_POST(self) -> None:
            content_length = int(self.headers.get("Content-Length", 0))
            req_data = json.loads(self.rfile.read(content_length).decode())
            method = req_data.get("method")

            if method == "rpc_error":
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                body = json.dumps(
                    {"jsonrpc": "2.0", "id": 1, "error": {"code": -32000, "message": "fail"}}
                ).encode()
            elif method == "http_500":
                self.send_response(500)
                self.send_header("Content-Type", "application/json")
                body = b'{"error": "internal error"}'
            else:
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                body = json.dumps({"jsonrpc": "2.0", "id": 1, "result": "ok"}).encode()

            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, format: str, *args: Any) -> None:
            pass

    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), ErrorHandler)
    server_thread = threading.Thread(target=server.serve_forever, daemon=True)
    server_thread.start()

    try:
        host, port = server.server_address
        rpc = http_rpc(f"http://{host}:{port}")

        with pytest.raises(RuntimeError, match="rpc_error failed"):
            rpc("rpc_error", [])

        with pytest.raises(RuntimeError, match="http_500 failed with HTTP 500"):
            rpc("http_500", [])
    finally:
        server.shutdown()
        server.server_close()
