"""
serve.py — Preview the web map locally: http://localhost:8000

Python's built-in http.server ignores HTTP Range requests, which PMTiles
needs (the browser fetches only the byte ranges of the tiles in view);
this adds them. GitHub Pages supports Range requests itself.

Usage:
    python web/serve.py
"""

import os
import re
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer


class RangeHandler(SimpleHTTPRequestHandler):
    def send_head(self):
        m = re.match(r"bytes=(\d+)-(\d*)$", self.headers.get("Range", ""))
        path = self.translate_path(self.path)
        if not m or not os.path.isfile(path):
            return super().send_head()
        size = os.path.getsize(path)
        start = int(m.group(1))
        end = min(int(m.group(2)) if m.group(2) else size - 1, size - 1)
        if start >= size:
            self.send_error(416)
            return None
        f = open(path, "rb")
        f.seek(start)
        self.send_response(206)
        self.send_header("Content-Type", self.guess_type(path))
        self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
        self.send_header("Content-Length", str(end - start + 1))
        self.send_header("Accept-Ranges", "bytes")
        self.end_headers()
        self.range_left = end - start + 1
        return f

    def copyfile(self, source, outputfile):
        left = getattr(self, "range_left", None)
        if left is None:
            return super().copyfile(source, outputfile)
        while left > 0:
            chunk = source.read(min(64 * 1024, left))
            if not chunk:
                break
            outputfile.write(chunk)
            left -= len(chunk)


if __name__ == "__main__":
    root = os.path.dirname(os.path.abspath(__file__))
    print("Serving", root, "at http://localhost:8000  (Ctrl+C to stop)")
    ThreadingHTTPServer(("", 8000), partial(RangeHandler, directory=root)).serve_forever()
