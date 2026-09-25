"""数据库连接与会话管理。"""
from sqlalchemy import create_engine
from sqlalchemy.engine import Engine
from sqlalchemy.orm import declarative_base, sessionmaker

from .config import DATABASE_URL

Base = declarative_base()

# SQLite 需要关闭同线程检查以配合 FastAPI 的依赖注入
engine: Engine = create_engine(
    DATABASE_URL,
    connect_args={"check_same_thread": False},
)

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


def configure_engine(url: str) -> Engine:
    """切换全局数据库引擎与会话工厂（测试隔离用）。

    替换模块级 ``engine`` 与 ``SessionLocal``，使已导入的 ``get_db``、
    ``seed`` 等模块随后都使用新绑定的数据库。
    """
    global engine, SessionLocal
    engine.dispose()
    engine = create_engine(url, connect_args={"check_same_thread": False})
    SessionLocal.configure(bind=engine)
    return engine


def get_db():
    """FastAPI 依赖：提供一个数据库会话，请求结束后关闭。"""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
