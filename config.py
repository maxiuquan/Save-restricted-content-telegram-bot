# Copyright @juktijol
# Channel t.me/juktijol
import os
from pathlib import Path
from dotenv import load_dotenv

# 使用绝对路径加载 .env，override=True 确保 .env 值覆盖系统环境变量
_env_path = Path(__file__).resolve().parent / ".env"
load_dotenv(dotenv_path=_env_path, override=True)

# Telegram API 凭据
def _get_int(name: str, default: int = 0) -> int:
    val = os.getenv(name, str(default))
    try:
        return int(val)
    except ValueError:
        print(f"\n{'='*60}")
        print(f"[config] 错误: .env 中 {name} 的值无效")
        print(f"[config] {name}={val!r}")
        print(f"[config] {name} 必须是纯数字，不能包含字母或符号")
        print(f"[config] 请从 https://my.telegram.org/apps 获取正确的 API_ID")
        print(f"{'='*60}\n")
        raise

API_ID = _get_int("API_ID")
API_HASH = os.getenv("API_HASH", "")
BOT_TOKEN = os.getenv("BOT_TOKEN", "")

# 管理员
DEVELOPER_USER_ID = _get_int("DEVELOPER_USER_ID")

# 数据库
MONGO_URL = os.getenv("MONGO_URL", "")
DATABASE_URL = os.getenv("DATABASE_URL", "") or MONGO_URL
DB_URL = os.getenv("DB_URL", "") or MONGO_URL

# 功能
LOG_GROUP_ID = _get_int("LOG_GROUP_ID")
FORCE_SUB_CHANNEL = os.getenv("FORCE_SUB_CHANNEL", "")
COMMAND_PREFIX = os.getenv("COMMAND_PREFIX", "!|.|#|,|/")
TELETHON_SESSION = os.getenv("TELETHON_SESSION", "")  # Telethon 备用下载器 session

# 第三方
FILELION_API = os.getenv("FILELION_API", "")
STREAMWISH_API = os.getenv("STREAMWISH_API", "")