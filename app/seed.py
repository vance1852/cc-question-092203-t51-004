"""启动时初始化数据库：建表 + 内置管理员 + 种子业务数据。

设计要点：
- 每类种子数据都有稳定业务标识（站点名 / 车牌 / 换电业务键），按标识
  逐条独立检查，绝不以“表中已有行数”来判断是否需要补数据——部分缺失
  （例如从不完整备份恢复后只剩站点表）也能把缺的车辆、换电记录补齐，
  而已有的站点及其配置保持原样、不被覆盖。
- 每条种子数据在独立事务中补齐并立即提交；初始化中途失败后重试，
  已提交的条目不会重复插入。
- 初始化使用独立引擎，其事务以 BEGIN IMMEDIATE 开始，立即取得 SQLite
  写锁；两个进程同时启动时，未抢到锁的一方会等锁释放后再按稳定标识
  复查，因此竞争初始化也不会产生重复数据。
"""
import time
from datetime import datetime, timedelta

from sqlalchemy import create_engine, event
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session, sessionmaker

from .auth import hash_password
from .config import DATABASE_URL, DEFAULT_ADMIN_PASSWORD, DEFAULT_ADMIN_USERNAME
from .database import Base
from .models import Station, SwapRecord, User, Vehicle

# 并发初始化时等待写锁的最长时间（秒）
_LOCK_TIMEOUT_SECONDS = 15

# 专用初始化引擎：所有事务以 BEGIN IMMEDIATE 启动（SQLAlchemy 官方
# SQLite 配方），把每条种子数据的“检查 + 插入”纳入写锁临界区。
_seed_engine = create_engine(
    DATABASE_URL,
    connect_args={"check_same_thread": False, "timeout": _LOCK_TIMEOUT_SECONDS},
)


@event.listens_for(_seed_engine, "connect")
def _disable_driver_autocommit(dbapi_connection, _record):
    # 让驱动不要自动发 BEGIN，事务起点完全交给下面的 begin 监听器控制。
    dbapi_connection.isolation_level = None


@event.listens_for(_seed_engine, "begin")
def _begin_immediate(conn):
    conn.exec_driver_sql("BEGIN IMMEDIATE")


SeedSessionLocal = sessionmaker(bind=_seed_engine, autocommit=False, autoflush=False)


def init_db() -> None:
    """创建所有表并按稳定业务标识补齐缺失的种子数据（可重复执行）。"""
    Base.metadata.create_all(bind=_seed_engine)
    for seeder in (_seed_admin, _seed_stations, _seed_vehicles, _seed_swaps):
        seeder()


def dispose() -> None:
    """释放初始化专用引擎的连接（主要供测试清理临时数据库时使用）。"""
    _seed_engine.dispose()


def _with_write_lock(insert):
    """在写锁保护下执行一次“按标识检查、缺失则插入”。

    每条种子数据独立事务、独立提交：抢锁冲突时重试并重新检查，因此
    重复启动、两个进程竞争初始化、初始化中断后重试都不会产生重复项。
    """
    deadline = time.monotonic() + _LOCK_TIMEOUT_SECONDS
    while True:
        db: Session = SeedSessionLocal()
        try:
            inserted = insert(db)
            db.commit()
            return inserted
        except OperationalError:
            # database is locked：另一个进程正在初始化，等锁后重新检查
            db.rollback()
            if time.monotonic() >= deadline:
                raise
            time.sleep(0.05)
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()


def _seed_admin() -> None:
    def insert(db: Session) -> bool:
        if db.query(User).filter(User.username == DEFAULT_ADMIN_USERNAME).first():
            return False
        db.add(
            User(
                username=DEFAULT_ADMIN_USERNAME,
                password_hash=hash_password(DEFAULT_ADMIN_PASSWORD),
                display_name="平台管理员",
            )
        )
        return True

    _with_write_lock(insert)


# ---- 种子数据定义（以稳定业务标识为键，顺序固定） ---------------------------

STATION_SEEDS = [
    {"key": "城东物流园换电站", "name": "城东物流园换电站", "address": "城东大道 128 号",
     "slot_total": 20, "battery_ready": 14, "status": "running"},
    {"key": "临港枢纽换电站", "name": "临港枢纽换电站", "address": "临港四路 9 号",
     "slot_total": 16, "battery_ready": 11, "status": "running"},
    {"key": "北郊配送中心换电站", "name": "北郊配送中心换电站", "address": "北环高速出口 3 公里",
     "slot_total": 12, "battery_ready": 4, "status": "maintenance"},
    {"key": "高新园区换电站", "name": "高新园区换电站", "address": "科创路 66 号",
     "slot_total": 24, "battery_ready": 20, "status": "running"},
]

VEHICLE_SEEDS = [
    {"key": "沪EV1234", "plate": "沪EV1234", "model": "远程星瀚 H",
     "battery_capacity": 141.0, "current_soc": 82.0, "status": "running"},
    {"key": "沪EV5678", "plate": "沪EV5678", "model": "比亚迪 T5",
     "battery_capacity": 100.0, "current_soc": 23.0, "status": "charging"},
    {"key": "苏EV9012", "plate": "苏EV9012", "model": "江淮恺达 EX8",
     "battery_capacity": 120.0, "current_soc": 56.0, "status": "idle"},
    {"key": "浙EV3456", "plate": "浙EV3456", "model": "开瑞优优 EV",
     "battery_capacity": 42.0, "current_soc": 9.0, "status": "fault"},
    {"key": "沪EV7788", "plate": "沪EV7788", "model": "远程星智 G",
     "battery_capacity": 160.0, "current_soc": 95.0, "status": "running"},
]

# 换电记录的稳定业务键：(车牌, 站点名, 换电前电量, 换电后电量, 距基准时间)。
# 不依赖自增主键，即使车辆/站点是分多次补齐的也能正确关联。
SWAP_SEEDS = [
    ("沪EV1234", "城东物流园换电站", 12.0, 100.0, timedelta(hours=2)),
    ("沪EV5678", "临港枢纽换电站", 8.0, 98.0, timedelta(hours=5)),
    ("苏EV9012", "城东物流园换电站", 15.0, 100.0, timedelta(days=1, hours=1)),
    ("沪EV7788", "高新园区换电站", 20.0, 100.0, timedelta(minutes=40)),
]

# 种子时间基准固定不变，使换电记录的业务键在多次启动间保持稳定，
# 不随当前时间漂移。
_SEED_BASE_TIME = datetime(2026, 1, 1, 0, 0, 0)


def _seed_stations() -> None:
    for spec in STATION_SEEDS:
        key = spec["key"]
        values = {k: v for k, v in spec.items() if k != "key"}

        def insert(db: Session, key=key, values=values) -> bool:
            # 按稳定业务标识（站名）独立检查，已存在则原样保留，不覆盖配置
            if db.query(Station).filter(Station.name == key).first():
                return False
            db.add(Station(**values))
            return True

        _with_write_lock(insert)


def _seed_vehicles() -> None:
    for spec in VEHICLE_SEEDS:
        key = spec["key"]
        values = {k: v for k, v in spec.items() if k != "key"}

        def insert(db: Session, key=key, values=values) -> bool:
            # 按稳定业务标识（车牌）独立检查
            if db.query(Vehicle).filter(Vehicle.plate == key).first():
                return False
            db.add(Vehicle(**values))
            return True

        _with_write_lock(insert)


def _seed_swaps() -> None:
    for plate, station_name, soc_before, soc_after, offset in SWAP_SEEDS:
        swapped_at = _SEED_BASE_TIME - offset

        def insert(db: Session) -> bool:
            vehicle = db.query(Vehicle).filter(Vehicle.plate == plate).first()
            station = db.query(Station).filter(Station.name == station_name).first()
            # 关联的车辆/站点尚不存在时跳过（正常种子顺序下均已存在）；
            # 之后再次启动时会自然补齐，不靠自增主键猜测关联。
            if vehicle is None or station is None:
                return False
            # 按稳定业务标识（车辆 + 站点 + 换电时间）独立检查，
            # 不用“记录表非空”这类会掩盖部分缺失的判断。
            exists = (
                db.query(SwapRecord)
                .filter(
                    SwapRecord.vehicle_id == vehicle.id,
                    SwapRecord.station_id == station.id,
                    SwapRecord.swapped_at == swapped_at,
                )
                .first()
            )
            if exists:
                return False
            db.add(
                SwapRecord(
                    vehicle_id=vehicle.id,
                    station_id=station.id,
                    soc_before=soc_before,
                    soc_after=soc_after,
                    swapped_at=swapped_at,
                )
            )
            return True

        _with_write_lock(insert)
