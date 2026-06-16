"""
配置文件 - 从环境变量加载所有配置值
支持 .env 文件和环境变量两种方式
"""
import os
import sys
from pathlib import Path
from dotenv import load_dotenv

# 加载 .env 文件（override=True 强制覆盖系统环境变量）
_env_path = Path(__file__).resolve().parent / ".env"

# 诊断：打印加载状态
import dotenv as _dotenv_module
print(f"[config] python-dotenv version: {getattr(_dotenv_module, '__version__', 'unknown')}")
print(f"[config] .env path: {_env_path}")
print(f"[config] .env exists: {_env_path.exists()}")

if _env_path.exists():
    # 兼容旧版 python-dotenv：手动读取再注入
    loaded = load_dotenv(_env_path, override=True)
    print(f"[config] load_dotenv returned: {loaded}")
    # 如果 override 不生效，手动强制覆盖
    if not os.environ.get("API_ID"):
        print("[config] override may not have worked, trying manual override...")
        load_dotenv(_env_path)  # 先正常加载
        # 如果还是不生效，直接读取文件
        if not os.environ.get("API_ID"):
            print("[config] Still no API_ID, reading .env file manually...")
            with open(_env_path, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if line and not line.startswith("#") and "=" in line:
                        key, _, val = line.partition("=")
                        key = key.strip()
                        val = val.strip().strip('"').strip("'")
                        if key and val:
                            os.environ[key] = val
                            print(f"[config] Manual set: {key}={val[:20] if key == 'API_HASH' else val}")
else:
    print(f"[config] WARNING: .env not found at {_env_path}")
    print(f"[config] CWD: {Path.cwd()}")
    load_dotenv(override=True)

# 诊断：打印加载后的值
print(f"[config] Final: API_ID={os.environ.get('API_ID', 'NOT_FOUND')}, BOT_TOKEN={'SET' if os.environ.get('BOT_TOKEN') else 'NOT_FOUND'}")


def get_int(name: str, default: int = 0) -> int:
    val = os.environ.get(name)
    if val is None:
        return default
    try:
        return int(val)
    except (ValueError, TypeError):
        return default


def get_str(name: str, default: str = "") -> str:
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