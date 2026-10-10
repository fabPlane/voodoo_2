#!/usr/bin/env python3
"""Live view of voodoo-stream output in a browser (MJPEG over HTTP).

    sudo ./voodoo-stream | ./voodoo-view.py [--port 8090] [--record out.mp4]

Reads 720x480 (--size) rgb565le frames on stdin; http://<host>:8090/ shows the latest
frame. --record also encodes every frame to an MP4 via ffmpeg (at --fps).
"""
import argparse
import io
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from PIL import Image


ap = argparse.ArgumentParser()
ap.add_argument("--port", type=int, default=8090)
ap.add_argument("--record")
ap.add_argument("--fps", type=float, default=60)
ap.add_argument("--quality", type=int, default=80)
ap.add_argument("--size", default="720x480", help="must match voodoo-stream --size")
args = ap.parse_args()
W, H = map(int, args.size.split("x"))
FRAME = W * H * 2

latest = None  # (seq, jpeg bytes)
cond = threading.Condition()

PAGE = b"""<!doctype html><title>SpinalVoodoo live</title>
<body style="background:#111;color:#ccc;font-family:sans-serif;text-align:center">
<h3>SpinalVoodoo on the Tang Mega 138K &mdash; live HDMI over PCIe DMA</h3>
<img src="/stream" style="width:960px;image-rendering:pixelated"></body>"""


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_GET(self):
        if self.path == "/":
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.end_headers()
            self.wfile.write(PAGE)
        elif self.path.startswith("/frame.jpg"):
            with cond:
                cur = latest
            if not cur:
                self.send_error(503)
                return
            self.send_response(200)
            self.send_header("Content-Type", "image/jpeg")
            self.end_headers()
            self.wfile.write(cur[1])
        elif self.path == "/stream":
            self.send_response(200)
            self.send_header("Content-Type", "multipart/x-mixed-replace; boundary=f")
            self.end_headers()
            seen = -1
            try:
                while True:
                    with cond:
                        cond.wait_for(lambda: latest and latest[0] != seen, timeout=5)
                        cur = latest
                    if not cur or cur[0] == seen:
                        continue
                    seen = cur[0]
                    self.wfile.write(b"--f\r\nContent-Type: image/jpeg\r\nContent-Length: %d\r\n\r\n" % len(cur[1]))
                    self.wfile.write(cur[1] + b"\r\n")
            except (BrokenPipeError, ConnectionResetError):
                pass
        else:
            self.send_error(404)


srv = ThreadingHTTPServer(("0.0.0.0", args.port), Handler)
threading.Thread(target=srv.serve_forever, daemon=True).start()
print(f"[voodoo-view] http://0.0.0.0:{args.port}/", file=sys.stderr)

rec = None
if args.record:
    rec = subprocess.Popen(
        ["ffmpeg", "-loglevel", "error", "-y", "-f", "rawvideo", "-pix_fmt", "rgb565le",
         "-s", f"{W}x{H}", "-r", str(args.fps), "-i", "-", "-c:v", "libx264", "-preset", "veryfast",
         "-crf", "18", "-pix_fmt", "yuv420p", args.record], stdin=subprocess.PIPE)

src = sys.stdin.buffer
seq = 0
last_jpeg = 0.0
while True:
    data = src.read(FRAME)
    if not data or len(data) < FRAME:
        break
    if rec:
        rec.stdin.write(data)
    now = time.monotonic()
    if now - last_jpeg < 1 / 30:  # browser view capped at 30 fps
        continue
    last_jpeg = now
    img = Image.frombuffer("RGB", (W, H), data, "raw", "BGR;16", 0, 1)
    out = io.BytesIO()
    img.save(out, "JPEG", quality=args.quality)
    seq += 1
    with cond:
        latest = (seq, out.getvalue())
        cond.notify_all()
if rec:
    rec.stdin.close()
    rec.wait()
print("[voodoo-view] input ended", file=sys.stderr)
