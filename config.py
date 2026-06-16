# Copyright @juktijol
# Channel t.me/juktijol
import os
from pathlib import Path
from dotenv import load_dotenv

# 使用绝对路径加载 .env，override=True 确保 .env 值覆盖系统环境变量
_env_path = Path(__file__).resolve().parent / ".env"
load_dotenv(dotenv_path=_env_path, override=True)

# Telegram API 凭据
API_ID = int(os.getenv("API_ID", "0"))
API_HASH = os.getenv("API_HASH", "")
BOT_TOKEN = os.getenv("BOT_TOKEN", "")

# 管理员
DEVELOPER_USER_ID = int(os.getenv("DEVELOPER_USER_ID", "0"))

# 数据库
MONGO_URL = os.getenv("MONGO_URL", "")
DATABASE_URL = os.getenv("DATABASE_URL", "") or MONGO_URL
DB_URL = os.getenv("DB_URL", "") or MONGO_URL

# 功能
LOG_GROUP_ID = int(os.getenv("LOG_GROUP_ID", "0"))
FORCE_SUB_CHANNEL = os.getenv("FORCE_SUB_CHANNEL", "")
COMMAND_PREFIX = os.getenv("COMMAND_PREFIX", "!|.|#|,|/")

# 第三方
FILELION_API = os.getenv("FILELION_API", "")
STREAMWISH_API = os.getenv("STREAMWISH_API", "")