"""Serve the app on the local network and show a QR code, so a phone on the same Wi-Fi
can open it by scanning. Run from backend/:  uv run python scripts/serve_lan.py
"""

import socket
import subprocess

import qrcode


PORT = 8000


def get_lan_ip() -> str:
    # Connecting a UDP socket sends nothing; it only makes the OS pick the outbound interface.
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        sock.connect(("8.8.8.8", 80))
        return sock.getsockname()[0]


if __name__ == "__main__":
    url = f"http://{get_lan_ip()}:{PORT}"
    print("Server:", url)
    qrcode.make(url).show()

    subprocess.run(
        ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", str(PORT), "--reload"],
        check=False,
    )
