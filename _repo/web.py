import os
import time
import threading
from flask import Flask, jsonify

app = Flask(__name__)

# Render free tier: PORT env variable ব্যবহার করো
PORT = int(os.environ.get("PORT", 8000))

# Uptime tracking
_start_time = time.time()


@app.route('/')
def home():
    uptime = int(time.time() - _start_time)
    hours, remainder = divmod(uptime, 3600)
    minutes, seconds = divmod(remainder, 60)
    return (
        f"Restricted Content DL Bot is Running Successfully! \U0001f680\n"
        f"Uptime: {hours}h {minutes}m {seconds}s"
    )


@app.route('/health')
def health():
    """Render health check endpoint"""
    return jsonify({
        "status": "ok",
        "uptime": int(time.time() - _start_time),
    }), 200


def run():
    app.run(host="0.0.0.0", port=PORT)


def _keep_alive():
    """Render free tier spin-down এড়াতে self-ping"""
    import urllib.request
    url = f"http://0.0.0.0:{PORT}/health"
    while True:
        time.sleep(300)  # প্রতি ৫ মিনিটে
        try:
            urllib.request.urlopen(url, timeout=10)
        except Exception:
            pass


if __name__ == "__main__":
    # ১. ওয়েব সার্ভার ব্যাকগ্রাউন্ডে চালু করা হচ্ছে
    t = threading.Thread(target=run, daemon=True)
    t.start()

    # ২. Keep-alive thread (Render free tier spin-down এড়াতে)
    ka = threading.Thread(target=_keep_alive, daemon=True)
    ka.start()

    # ৩. এরপর আপনার মেইন বট বা start.sh রান করা হচ্ছে
    os.system("bash start.sh")
