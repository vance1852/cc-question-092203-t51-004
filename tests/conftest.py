"""pytest 全局配置：为测试强制使用隔离的临时数据库。

关键约束：
- 必须在导入任何 app.* 模块之前设置 DB_PATH 环境变量，因为
  app.config 在导入时就读取路径、app.database 在导入时就创建引擎。
- 临时数据库位于系统临时目录，与项目根目录的生产 data.db 完全隔离；
  测试的建表、清理、重置都只会作用于这个临时文件，绝不会清理生产数据。
"""
import os
import shutil
import tempfile

import pytest

# 1) 先指定隔离的临时数据库，再导入应用模块 -------------------------------
_TEST_DB_DIR = tempfile.mkdtemp(prefix="swap-station-test-")
_TEST_DB_PATH = os.path.join(_TEST_DB_DIR, "test.db")
os.environ["DB_PATH"] = _TEST_DB_PATH

from app.database import Base, engine  # noqa: E402
from app.seed import dispose as dispose_seed_engine, init_db  # noqa: E402


@pytest.fixture(autouse=True)
def _reset_database():
    """每个用例前把数据库重置为“干净表 + 完整种子”的可预测状态。

    用例中创建的车辆、站点、换电记录等不会残留到下一个用例，
    因此测试结果与执行顺序、执行次数无关，可连续反复运行。
    """
    Base.metadata.drop_all(bind=engine)
    init_db()
    yield


def pytest_sessionfinish(session, exitstatus):
    """整个测试会话结束后释放连接并删除临时数据库目录。"""
    engine.dispose()
    dispose_seed_engine()
    shutil.rmtree(_TEST_DB_DIR, ignore_errors=True)
