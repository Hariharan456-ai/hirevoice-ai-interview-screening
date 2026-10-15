import argparse, socket
from app import create_app

def lan_ip():
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM); s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]; s.close(); return ip
    except Exception:
        return "127.0.0.1"

if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--https", action="store_true", help="serve HTTPS (self-signed) so phones on your Wi-Fi can use the microphone")
    ap.add_argument("--port", type=int, default=5000)
    a = ap.parse_args()
    app = create_app()
    scheme = "https" if a.https else "http"
    print(f"\n  HR dashboard : {scheme}://127.0.0.1:{a.port}")
    print(f"  Same Wi-Fi   : {scheme}://{lan_ip()}:{a.port}   (candidates on phones need --https)\n")
    app.run(host="0.0.0.0", port=a.port, debug=False, use_reloader=False, threaded=True,
            ssl_context="adhoc" if a.https else None)
