#!/usr/bin/env python3
"""Tiny LAN webcam viewer for watching the Tang Console HDMI display."""

from __future__ import annotations

import argparse
import subprocess
import threading
import time
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


INDEX = b"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width,initial-scale=1">
  <title>Voodoo FPGA Camera</title>
  <style>
    :root { color-scheme: dark; }
    * { box-sizing: border-box; }
    body { margin: 0; min-height: 100vh; display: grid; place-items: center;
           background: #090b0e; color: #eee; font: 15px system-ui, sans-serif; }
    main { width: min(96vw, 1100px); }
    header { display: flex; justify-content: space-between; align-items: baseline;
             gap: 1rem; margin: 0 0 .65rem; }
    h1 { margin: 0; font-size: 1.05rem; font-weight: 600; }
    #state { color: #75e69a; font-variant-numeric: tabular-nums; }
    img { display: block; width: 100%; height: auto; background: #000;
          border: 1px solid #303640; border-radius: 8px; }
  </style>
</head>
<body><main>
  <header><h1>Voodoo FPGA camera</h1><span id="state">live</span></header>
  <img src="__STREAM_PATH__" alt="Live FPGA display">
</main></body>
</html>
"""


class Camera:
    def __init__(self, device: str, size: str, fps: int) -> None:
        self.device = device
        self.size = size
        self.fps = fps
        self.condition = threading.Condition()
        self.frame = b""
        self.sequence = 0
        self.error = "starting"

    def start(self) -> None:
        threading.Thread(target=self._run, name="camera", daemon=True).start()

    def _run(self) -> None:
        command = [
            "ffmpeg", "-hide_banner", "-loglevel", "error",
            "-f", "v4l2", "-video_size", self.size, "-i", self.device,
            "-vf", f"fps={self.fps}", "-q:v", "5",
            "-f", "image2pipe", "-vcodec", "mjpeg", "pipe:1",
        ]
        while True:
            process = subprocess.Popen(command, stdout=subprocess.PIPE)
            buffer = bytearray()
            try:
                assert process.stdout is not None
                while chunk := process.stdout.read(65536):
                    buffer.extend(chunk)
                    while True:
                        start = buffer.find(b"\xff\xd8")
                        end = buffer.find(b"\xff\xd9", start + 2)
                        if start < 0 or end < 0:
                            if start > 0:
                                del buffer[:start]
                            break
                        frame = bytes(buffer[start:end + 2])
                        del buffer[:end + 2]
                        with self.condition:
                            self.frame = frame
                            self.sequence += 1
                            self.error = ""
                            self.condition.notify_all()
            finally:
                process.kill()
                process.wait()
                with self.condition:
                    self.error = "camera restarting"
                    self.condition.notify_all()
                time.sleep(1)

    def wait(self, previous: int, timeout: float = 2.0) -> tuple[int, bytes]:
        with self.condition:
            self.condition.wait_for(lambda: self.sequence != previous, timeout)
            return self.sequence, self.frame


class Handler(BaseHTTPRequestHandler):
    camera: Camera
    token: str

    def do_GET(self) -> None:
        path = self.path.split("?", 1)[0]
        base = f"/{self.token}"
        if path in (base, base + "/"):
            body = INDEX.replace(b"__STREAM_PATH__", (base + "/stream.mjpg").encode())
            self._send(HTTPStatus.OK, "text/html; charset=utf-8", body)
        elif path == base + "/frame.jpg":
            _, frame = self.camera.wait(-1)
            if frame:
                self._send(HTTPStatus.OK, "image/jpeg", frame, cache=False)
            else:
                self._send(HTTPStatus.SERVICE_UNAVAILABLE, "text/plain", b"No frame yet\n")
        elif path == base + "/stream.mjpg":
            self._stream()
        elif path == "/healthz":
            status = HTTPStatus.OK if self.camera.frame else HTTPStatus.SERVICE_UNAVAILABLE
            body = ("ok\n" if self.camera.frame else self.camera.error + "\n").encode()
            self._send(status, "text/plain", body, cache=False)
        else:
            self._send(HTTPStatus.NOT_FOUND, "text/plain", b"Not found\n")

    def _send(self, status: HTTPStatus, content_type: str, body: bytes,
              cache: bool = True) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "public, max-age=30" if cache else "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _stream(self) -> None:
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "multipart/x-mixed-replace; boundary=frame")
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        sequence = -1
        try:
            while True:
                sequence, frame = self.camera.wait(sequence)
                if not frame:
                    continue
                self.wfile.write(
                    b"--frame\r\nContent-Type: image/jpeg\r\nContent-Length: "
                    + str(len(frame)).encode() + b"\r\n\r\n" + frame + b"\r\n"
                )
        except (BrokenPipeError, ConnectionResetError):
            pass

    def log_message(self, format: str, *args: object) -> None:
        return


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--device", default="/dev/video0")
    parser.add_argument("--size", default="640x480")
    parser.add_argument("--fps", type=int, default=8)
    parser.add_argument("--bind", required=True)
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--token", required=True)
    args = parser.parse_args()

    camera = Camera(args.device, args.size, args.fps)
    camera.start()
    Handler.camera = camera
    Handler.token = args.token
    server = ThreadingHTTPServer((args.bind, args.port), Handler)
    print(f"Voodoo webcam: http://{args.bind}:{args.port}/{args.token}/", flush=True)
    server.serve_forever()


if __name__ == "__main__":
    main()
