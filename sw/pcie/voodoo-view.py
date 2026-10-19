#!/usr/bin/env python3
"""Live view of voodoo-stream output in a browser (MJPEG over HTTP).

    sudo ./voodoo-stream | ./voodoo-view.py [--port 8090] [--record out.mp4]

Reads 720x480 (--size) rgb565le frames on stdin; http://<host>:8090/ shows the latest
frame. --record also encodes every frame to an MP4 via ffmpeg (at --fps).

--input-port N makes the page interactive: keys and (after a click, pointer
lock) the mouse are POSTed to /input and forwarded as UDP datagrams to
127.0.0.1:N, where GLQuake reads them when run with QUAKE_INPUT_UDP=N (see
sw/quake-glide/stubs/webinput.h).
"""
import argparse
import io
import socket
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
ap.add_argument("--input-port", type=int, help="forward browser input to this local UDP port")
args = ap.parse_args()
W, H = map(int, args.size.split("x"))
FRAME = W * H * 2

latest = None  # (seq, jpeg bytes)
cond = threading.Condition()

PAGE = b"""<!doctype html><title>SpinalVoodoo live</title>
<body style="background:#111;color:#ccc;font-family:sans-serif;text-align:center">
<h3>SpinalVoodoo on the Tang Mega 138K &mdash; live HDMI over PCIe DMA</h3>
<img id="v" src="/stream" style="width:960px;image-rendering:pixelated">
<p id="s" style="font-size:14px"></p>
<script>
const s = document.getElementById("s"), v = document.getElementById("v");
fetch("/input", {method: "POST", body: ""}).then(r => {
  if (!r.ok) { s.textContent = "view only"; return; }
  s.textContent = "Keyboard goes to the game. Click the picture to capture the mouse (Esc releases it).";
  // KeyboardEvent.code -> Linux keycode (what svgalib's raw keyboard reports).
  const K = {Escape:1,Digit1:2,Digit2:3,Digit3:4,Digit4:5,Digit5:6,Digit6:7,Digit7:8,Digit8:9,
    Digit9:10,Digit0:11,Minus:12,Equal:13,Backspace:14,Tab:15,KeyQ:16,KeyW:17,KeyE:18,KeyR:19,
    KeyT:20,KeyY:21,KeyU:22,KeyI:23,KeyO:24,KeyP:25,BracketLeft:26,BracketRight:27,Enter:28,
    ControlLeft:29,KeyA:30,KeyS:31,KeyD:32,KeyF:33,KeyG:34,KeyH:35,KeyJ:36,KeyK:37,KeyL:38,
    Semicolon:39,Quote:40,Backquote:41,ShiftLeft:42,Backslash:43,KeyZ:44,KeyX:45,KeyC:46,KeyV:47,
    KeyB:48,KeyN:49,KeyM:50,Comma:51,Period:52,Slash:53,ShiftRight:54,AltLeft:56,Space:57,
    F1:59,F2:60,F3:61,F4:62,F5:63,F6:64,F7:65,F8:66,F9:67,F10:68,F11:87,F12:88,NumpadEnter:28,
    ControlRight:97,AltRight:100,Home:102,ArrowUp:103,PageUp:104,ArrowLeft:105,ArrowRight:106,
    End:107,ArrowDown:108,PageDown:109,Insert:110,Delete:111,Pause:119};
  const send = m => fetch("/input", {method: "POST", body: m});
  const down = new Set();
  const key = (e, d) => {
    const k = K[e.code];
    if (!k) return;
    e.preventDefault();
    if (d && down.has(k)) return;  // no auto-repeat
    d ? down.add(k) : down.delete(k);
    send("k " + k + " " + (d ? 1 : 0));
  };
  addEventListener("keydown", e => key(e, true));
  addEventListener("keyup", e => key(e, false));
  addEventListener("blur", () => { down.forEach(k => send("k " + k + " 0")); down.clear(); });
  let dx = 0, dy = 0, btn = 0, dirty = false;
  const BIT = [4, 2, 1];  // browser left/middle/right -> svgalib bits
  v.addEventListener("click", () => v.requestPointerLock());
  v.addEventListener("contextmenu", e => e.preventDefault());
  addEventListener("mousemove", e => {
    if (document.pointerLockElement !== v) return;
    dx += e.movementX; dy += e.movementY; dirty = true;
  });
  addEventListener("mousedown", e => {
    if (document.pointerLockElement !== v) return;
    btn |= BIT[e.button] || 0; dirty = true;
  });
  addEventListener("mouseup", e => { btn &= ~(BIT[e.button] || 0); dirty = true; });
  setInterval(() => {
    if (!dirty) return;
    send("m " + btn + " " + dx + " " + dy);
    dx = dy = 0; dirty = false;
  }, 50);
});
</script></body>"""


inp = socket.socket(socket.AF_INET, socket.SOCK_DGRAM) if args.input_port else None


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_POST(self):
        if self.path != "/input" or not inp:
            self.send_error(404)
            return
        msg = self.rfile.read(min(int(self.headers.get("Content-Length", 0)), 64))
        if msg:
            inp.sendto(msg, ("127.0.0.1", args.input_port))
        self.send_response(204)
        self.end_headers()

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
