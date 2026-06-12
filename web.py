import os
import time
import threading
from flask import Flask, jsonify

app = Flask(__name__)

# Render 免费层：使用 PORT 环境变量
PORT = int(os.environ.get("PORT", 8000))

# 运行时间跟踪
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
    """Render 免费层避免休眠的自我 ping"""
    import urllib.request
    url = f"http://0.0.0.0:{PORT}/health"
    while True:
        time.sleep(300)  # 每 5 分钟
        try:
            urllib.request.urlopen(url, timeout=10)
        except Exception:
            pass


if __name__ == "__main__":
    # 1. 在后台启动 Web 服务器
    t = threading.Thread(target=run, daemon=True)
    t.start()

    # 2. 保活线程（避免 Render 免费层休眠）
    ka = threading.Thread(target=_keep_alive, daemon=True)
    ka.start()

    # 3. 然后运行主机器人或 start.sh
    os.system("bash start.sh")
