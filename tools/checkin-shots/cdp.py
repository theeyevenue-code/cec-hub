"""Minimal Chrome DevTools Protocol client (stdlib only) for screenshots."""
import base64
import json
import os
import socket
import struct
import subprocess
import time
import urllib.request

CHROME = r"C:\Program Files\Google\Chrome\Application\chrome.exe"
PORT = 9333


class WS:
    def __init__(self, url):
        assert url.startswith("ws://")
        hostport, path = url[5:].split("/", 1)
        host, port = hostport.split(":")
        self.s = socket.create_connection((host, int(port)))
        key = base64.b64encode(os.urandom(16)).decode()
        req = (f"GET /{path} HTTP/1.1\r\nHost: {hostport}\r\nUpgrade: websocket\r\n"
               f"Connection: Upgrade\r\nSec-WebSocket-Key: {key}\r\nSec-WebSocket-Version: 13\r\n\r\n")
        self.s.sendall(req.encode())
        buf = b""
        while b"\r\n\r\n" not in buf:
            buf += self.s.recv(4096)
        self.rest = buf.split(b"\r\n\r\n", 1)[1]
        self.id = 0

    def _recv_exact(self, n):
        while len(self.rest) < n:
            chunk = self.s.recv(1 << 20)
            if not chunk:
                raise EOFError
            self.rest += chunk
        out, self.rest = self.rest[:n], self.rest[n:]
        return out

    def send_text(self, text):
        data = text.encode()
        hdr = bytearray([0x81])
        n = len(data)
        if n < 126:
            hdr.append(0x80 | n)
        elif n < 65536:
            hdr.append(0x80 | 126)
            hdr += struct.pack(">H", n)
        else:
            hdr.append(0x80 | 127)
            hdr += struct.pack(">Q", n)
        mask = os.urandom(4)
        hdr += mask
        self.s.sendall(bytes(hdr) + bytes(b ^ mask[i % 4] for i, b in enumerate(data)))

    def recv_text(self):
        msg = b""
        while True:
            b1, b2 = self._recv_exact(2)
            n = b2 & 0x7F
            if n == 126:
                n = struct.unpack(">H", self._recv_exact(2))[0]
            elif n == 127:
                n = struct.unpack(">Q", self._recv_exact(8))[0]
            payload = self._recv_exact(n)
            msg += payload
            if b1 & 0x80:
                return msg.decode()

    def call(self, method, **params):
        self.id += 1
        mid = self.id
        self.send_text(json.dumps({"id": mid, "method": method, "params": params}))
        while True:
            m = json.loads(self.recv_text())
            if m.get("id") == mid:
                if "error" in m:
                    raise RuntimeError(f"{method}: {m['error']}")
                return m.get("result", {})


class Browser:
    def __init__(self, profile):
        self.proc = subprocess.Popen([CHROME, "--headless=new", "--disable-gpu", "--no-first-run",
                                      f"--remote-debugging-port={PORT}", f"--user-data-dir={profile}",
                                      "--hide-scrollbars", "about:blank"],
                                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        for _ in range(50):
            try:
                tabs = json.loads(urllib.request.urlopen(f"http://127.0.0.1:{PORT}/json").read())
                page = next(t for t in tabs if t.get("type") == "page")
                self.ws = WS(page["webSocketDebuggerUrl"])
                break
            except Exception:
                time.sleep(0.2)
        self.ws.call("Page.enable")
        self.ws.call("Runtime.enable")

    def size(self, w, h, mobile=True):
        self.ws.call("Emulation.setDeviceMetricsOverride", width=w, height=h,
                     deviceScaleFactor=1, mobile=mobile)
        self.w, self.h = w, h

    def go(self, url, wait=1.5):
        self.ws.call("Page.navigate", url=url)
        time.sleep(wait)

    def js(self, expr, wait=0.4):
        r = self.ws.call("Runtime.evaluate", expression=expr, awaitPromise=True, returnByValue=True)
        time.sleep(wait)
        return r.get("result", {}).get("value")

    def shot(self, path, full=True):
        params = {"format": "png"}
        if full:
            m = self.ws.call("Page.getLayoutMetrics")
            cs = m.get("cssContentSize") or m["contentSize"]
            params["clip"] = {"x": 0, "y": 0, "width": self.w,
                              "height": max(self.h, int(cs["height"])), "scale": 1}
            params["captureBeyondViewport"] = True
        r = self.ws.call("Page.captureScreenshot", **params)
        with open(path, "wb") as f:
            f.write(base64.b64decode(r["data"]))
        return path

    def drag(self, points):
        x, y = points[0]
        self.ws.call("Input.dispatchMouseEvent", type="mousePressed", x=x, y=y, button="left", clickCount=1)
        for x, y in points[1:]:
            self.ws.call("Input.dispatchMouseEvent", type="mouseMoved", x=x, y=y, button="left", buttons=1)
        self.ws.call("Input.dispatchMouseEvent", type="mouseReleased", x=x, y=y, button="left", clickCount=1)

    def close(self):
        self.proc.terminate()
