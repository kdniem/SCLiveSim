"""Download logic against a local HTTP server that behaves like Mendeley Data."""

import hashlib
import threading
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import pytest

from adapters.dataco.fetch import RemoteFile, download, ensure_dataset

BODY = b"Order Id,Order Item Id\n1,1\n"


class _Handler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        # Mendeley answers 403 to urllib's default User-Agent.
        if self.headers.get("User-Agent", "").startswith("Python-urllib"):
            self.send_response(403)
            self.end_headers()
            return
        self.send_response(200)
        self.send_header("Content-Length", str(len(BODY)))
        self.end_headers()
        self.wfile.write(BODY)

    def log_message(self, *args: object) -> None:
        pass


@pytest.fixture
def server_url() -> Iterator[str]:
    server = HTTPServer(("127.0.0.1", 0), _Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_port}/file"
    server.shutdown()


def test_download_verifies_checksum(server_url: str, tmp_path: Path) -> None:
    good = RemoteFile("data.csv", server_url, hashlib.sha256(BODY).hexdigest())
    path = download(good, tmp_path)
    assert path.read_bytes() == BODY

    bad = RemoteFile("other.csv", server_url, "0" * 64)
    with pytest.raises(RuntimeError, match="checksum mismatch"):
        download(bad, tmp_path)
    assert not (tmp_path / "other.csv").exists()
    assert not (tmp_path / "other.part").exists()


def test_missing_dataset_without_auto_download(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError, match=r"adapters\.dataco\.fetch"):
        ensure_dataset(tmp_path, auto_download=False)
