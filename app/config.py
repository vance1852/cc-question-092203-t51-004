"""应用配置。

所有可调参数集中在这里，纯后端服务，使用本地 SQLite，离线可运行。
"""
import os

# 数据库文件路径（SQLite，本地文件，开箱即用）
# 可通过环境变量 APP_DB_PATH 覆盖；也可用 APP_DATABASE_URL 指定完整连接串
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB_PATH = os.environ.get("APP_DB_PATH", os.path.join(BASE_DIR, "data.db"))
DATABASE_URL = os.environ.get("APP_DATABASE_URL", f"sqlite:///{DB_PATH}")

# JWT 配置
SECRET_KEY = os.getenv("APP_SECRET_KEY", "swap-station-admin-dev-secret-key-change-me")
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = 60 * 12  # 12 小时

# 内置管理员账号（首次启动自动初始化）
DEFAULT_ADMIN_USERNAME = "admin"
DEFAULT_ADMIN_PASSWORD = "admin123"

# 服务端口（使用非常见端口）
APP_PORT = 7634
