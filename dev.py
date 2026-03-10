import subprocess
import qrcode
import socket

def get_ip():
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.connect(("8.8.8.8",80))
    ip = s.getsockname()[0]
    s.close()
    return ip

ip = get_ip()
url = f"http://{ip}:8000"

print("Server:", url)

img = qrcode.make(url)
img.show()

subprocess.run([
    "uvicorn", "main:app",
    "--host", "0.0.0.0",
    "--port", "8000",
    "--reload"
])