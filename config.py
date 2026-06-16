"""
配置文件 - 从环境变量加载所有配置值
支持 .env 文件和环境变量两种方式
"""
import os
from pathlib import Path
from dotenv import load_dotenv

# 加载 .env 文件（使用绝对路径，确保无论从哪个目录启动都能找到）
# override=True 强制覆盖已存在的环境变量，防止系统环境变量干扰
_env_path = Path(__file__).parent / ".env"
if _env_path.exists():
    load_dotenv(_env_path, override=True)
else:
    # 回退：从当前工作目录及父目录搜索 .env
    load_dotenv(override=True)

# 调试：打印加载来源和关键值
import logging
_log = logging.getLogger("config")
_log.info(
    "env_path=%s exists=%s API_ID=%s BOT_TOKEN=%s",
    _env_path, _env_path.exists(),
    os.environ.get("API_ID", "NOT_SET"),
    "SET" if os.environ.get("BOT_TOKEN") else "NOT_SET",
)


def get_int(name: str, default: int = 0) -> int:
    """安全地获取整型配置值"""
    val = os.environ.get(name)
    if val is None:
        return default
    try:
        return int(val)
    except (ValueError, TypeError):
        return default


def get_str(name: str, default: str = "") -> str:
    """安全地获取字符串配置值"""
    return os.environ.get(name, default)


# ═══════════════════════════════════════════
# Telegram API 凭据（必填）
# ═══════════════════════════════════════════
API_ID: int = get_int("API_ID")
API_HASH: str = get_str("API_HASH")
BOT_TOKEN: str = get_str("BOT_TOKEN")

# ═══════════════════════════════════════════
# 管理员和用户 ID
# ═══════════════════════════════════════════
DEVELOPER_USER_ID: int = get_int("DEVELOPER_USER_ID")

# ═══════════════════════════════════════════
# 数据库配置
# ═══════════════════════════════════════════
MONGO_URL: str = get_str("MONGO_URL")
DATABASE_URL: str = get_str("DATABASE_URL") or MONGO_URL
DB_URL: str = get_str("DB_URL") or MONGO_URL

# ═══════════════════════════════════════════
# 功能配置
# ═══════════════════════════════════════════
LOG_GROUP_ID: int = get_int("LOG_GROUP_ID")
FORCE_SUB_CHANNEL: str = get_str("FORCE_SUB_CHANNEL")
COMMAND_PREFIX: str = get_str("COMMAND_PREFIX", "!|.|#|,|/")

# ═══════════════════════════════════════════
# 第三方服务 API Key
# ═══════════════════════════════════════════
FILELION_API: str = get_str("FILELION_API")
STREAMWISH_API: str = get_str("STREAMWISH_API")
