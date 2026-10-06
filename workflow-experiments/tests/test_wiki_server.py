from __future__ import annotations

import json
import socket
import threading
from pathlib import Path
from urllib.request import urlopen

from onectx.config import load_system
from onectx.wiki.server import create_wiki_server


def test_wiki_server_falls_back_when_requested_port_is_busy() -> None:
    system = load_system(Path.cwd())
    occupied = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    occupied.bind(("127.0.0.1", 0))
    occupied.listen(1)
    busy_port = occupied.getsockname()[1]

    try:
        server = create_wiki_server(system, host="127.0.0.1", port=busy_port, max_port_tries=4)
        try:
            assert server.server_address[1] != busy_port
            assert busy_port < server.server_address[1] <= busy_port + 3
        finally:
            server.server_close()
    finally:
        occupied.close()


def test_wiki_server_health_responds_over_http() -> None:
    system = load_system(Path.cwd())
    server = create_wiki_server(system, host="127.0.0.1", port=0)
    host, port = server.server_address[:2]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    try:
        with urlopen(f"http://{host}:{port}/__health", timeout=5) as response:
            payload = json.loads(response.read().decode("utf-8"))
        assert payload["status"] == "ok"
        assert "routes" in payload
        assert "manifests" in payload
    finally:
        server.shutdown()
        thread.join(timeout=5)
        server.server_close()


def test_wiki_server_stats_endpoint_responds_over_http() -> None:
    system = load_system(Path.cwd())
    server = create_wiki_server(system, host="127.0.0.1", port=0)
    host, port = server.server_address[:2]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    try:
        with urlopen(f"http://{host}:{port}/api/wiki/stats", timeout=5) as response:
            payload = json.loads(response.read().decode("utf-8"))
        assert payload["schema_version"] == "wiki.stats.v1"
        assert payload["totals"]["families"] >= 1
        assert "families" in payload
    finally:
        server.shutdown()
        thread.join(timeout=5)
        server.server_close()
