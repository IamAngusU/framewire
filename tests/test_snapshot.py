from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread

import cv2
import numpy as np

from framewire.sources import SnapshotSource


def test_http_snapshot_decodes_real_jpeg():
    image = np.full((24, 32, 3), 180, dtype=np.uint8)
    ok, encoded = cv2.imencode(".jpg", image)
    assert ok

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.send_header("Content-Type", "image/jpeg")
            self.end_headers()
            self.wfile.write(encoded.tobytes())

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    worker = Thread(target=server.serve_forever, daemon=True)
    worker.start()
    try:
        source = SnapshotSource(f"http://127.0.0.1:{server.server_port}/frame.jpg", "local")
        assert source.read().shape == (24, 32, 3)
    finally:
        server.shutdown()
        server.server_close()
        worker.join(timeout=2)
