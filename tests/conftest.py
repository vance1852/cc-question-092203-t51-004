"""测试共用夹具：每个用例使用独立的临时数据库，互不影响，也绝不触碰生产 data.db。"""
import hashlib
import sys
from pathlib import Path

import pytest

# 仓库根目录（保证 import 到 app 包）
ROOT_DIR = Path(__file__).resolve().parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from app import database  # noqa: E402
from app.config import DB_PATH as PROD_DB_PATH  # noqa: E402
from app.seed import init_db  # noqa: E402


def _snapshot_prod_db():
    """返回生产数据库文件当前内容指纹（不存在记为 None）。"""
    path = Path(PROD_DB_PATH)
    if not path.exists():
        return None
    return hashlib.sha256(path.read_bytes()).hexdigest()


@pytest.fixture(scope="session", autouse=True)
def _protect_production_db():
    """整个测试会话前后生产数据文件必须保持不变。"""
    before = _snapshot_prod_db()
    yield
    after = _snapshot_prod_db()
    assert before == after, "测试运行改动了生产数据库 data.db"


@pytest.fixture()
def isolated_db(tmp_path):
    """每个用例独立的临时 SQLite 文件：建表 + 种子初始化，用例结束即丢弃。"""
    db_file = tmp_path / "test.db"
    database.configure_engine(f"sqlite:///{db_file}")
    database.Base.metadata.drop_all(bind=database.engine)
    database.Base.metadata.create_all(bind=database.engine)
    init_db()
    try:
        yield db_file
    finally:
        database.engine.dispose()
