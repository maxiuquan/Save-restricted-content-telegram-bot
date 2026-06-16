"""
配置文件 - 手动解析 .env，不依赖 python-dotenv 的 override 行为
"""
import os
import sys
from pathlib import Path


def _load_env_file():
    """手动解析 .env 文件，逐行读取注入 os.environ。"""
    # 搜索路径优先级
    search_paths = [
        Path(__file__).resolve().parent / ".env",       # config.py 同级目录
        Path.cwd() / ".env",                             # 当前工作目录
    ]
    if sys.argv and sys.argv[0]:
        search_paths.append(Path(sys.argv[0]).resolve().parent / ".env")

    env_file = None
    for p in search_paths:
        if p.is_file():
            env_file = p
            break

    if env_file is None:
        print(f"[config] .env NOT FOUND. Searched: {[str(p) for p in search_paths]}")
        return

    print(f"[config] Loading .env from: {env_file}")
    count = 0
    with open(env_file, "r", encoding="utf-8-sig") as f:
        for line in f:
            line = line.strip()
            # 跳过空行和注释
            if not line or line.startswith("#"):
                continue
            # 解析 KEY=VALUE（支持带引号的值）
            if "=" not in line:
                continue
            key, _, val = line.partition("=")
            key = key.strip()
            val = val.strip()
            # 去除引号
            if val and val[0] in ('"', "'") and val[-1] == val[0]:
                val = val[1:-1]
            if key and val:
                os.environ[key] = val
                count += 1

    print(f"[config] Loaded {count} variables from .env")
    print(f"[config] API_ID={os.environ.get('API_ID', 'NOT_FOUND')}")
    print(f"[config] BOT_TOKEN={'SET' if os.environ.get('BOT_TOKEN') else 'NOT_FOUND'}")


# 立即加载 .env
_load_env_file()


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


# Telegram API 凭据
API_ID: int = get_int("API_ID")
API_HASH: str = get_str("API_HASH")
BOT_TOKEN: str = get_str("BOT_TOKEN")

# 管理员
DEVELOPER_USER_ID: int = get_int("DEVELOPER_USER_ID")

# 数据库
MONGO_URL: str = get_str("MONGO_URL")
DATABASE_URL: str = get_str("DATABASE_URL") or MONGO_URL
DB_URL: str = get_str("DB_URL") or MONGO_URL

# 功能
LOG_GROUP_ID: int = get_int("LOG_GROUP_ID")
FORCE_SUB_CHANNEL: str = get_str("FORCE_SUB_CHANNEL")
COMMAND_PREFIX: str = get_str("COMMAND_PREFIX", "!|.|#|,|/")

# 第三方
FILELION_API: str = get_str("FILELION_API")
STREAMWISH_API: str = get_str("STREAMWISH_API")