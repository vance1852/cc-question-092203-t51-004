"""首次启动时初始化数据库：建表 + 内置管理员 + 种子业务数据。

种子补齐策略：
- 每一类、每一条种子都按稳定业务标识独立判断是否存在
  （站点按名称、车辆按车牌、换电记录按“车 + 站 + 换电前后电量”），
  绝不用“表中有行”掩盖部分缺失；
- 已有站点及其配置保持原样，只补齐缺失的车辆与记录；
- 全部检查与插入在单个事务内提交，中断重试不会留下半成品；
- 并发初始化时靠唯一约束与事务回滚兜底，冲突后重试，不重复插入。
"""
from datetime import datetime, timedelta

from sqlalchemy.exc import IntegrityError, OperationalError

from . import database
from .auth import hash_password
from .config import DEFAULT_ADMIN_PASSWORD, DEFAULT_ADMIN_USERNAME
from .models import Station, SwapRecord, User, Vehicle

# 种子换电时间使用固定基准时刻，保证业务标识与内容在多次启动间稳定
# （不使用 datetime.utcnow()，否则每次启动生成的记录都无法被识别为同一条）。
_SEED_BASE_TIME = datetime(2026, 9, 25, 8, 0, 0)

STATION_SEEDS = [
    dict(name="城东物流园换电站", address="城东大道 128 号", slot_total=20, battery_ready=14, status="running"),
    dict(name="临港枢纽换电站", address="临港四路 9 号", slot_total=16, battery_ready=11, status="running"),
    dict(name="北郊配送中心换电站", address="北环高速出口 3 公里", slot_total=12, battery_ready=4, status="maintenance"),
    dict(name="高新园区换电站", address="科创路 66 号", slot_total=24, battery_ready=20, status="running"),
]

VEHICLE_SEEDS = [
    dict(plate="沪EV1234", model="远程星瀚 H", battery_capacity=141.0, current_soc=82.0, status="running"),
    dict(plate="沪EV5678", model="比亚迪 T5", battery_capacity=100.0, current_soc=23.0, status="charging"),
    dict(plate="苏EV9012", model="江淮恺达 EX8", battery_capacity=120.0, current_soc=56.0, status="idle"),
    dict(plate="浙EV3456", model="开瑞优优 EV", battery_capacity=42.0, current_soc=9.0, status="fault"),
    dict(plate="沪EV7788", model="远程星智 G", battery_capacity=160.0, current_soc=95.0, status="running"),
]

# 换电记录种子：(车辆车牌, 站点名称, 换电前电量, 换电后电量, 相对基准时刻偏移)
SWAP_SEEDS = [
    ("沪EV1234", "城东物流园换电站", 12.0, 100.0, timedelta(hours=2)),
    ("沪EV5678", "临港枢纽换电站", 8.0, 98.0, timedelta(hours=5)),
    ("苏EV9012", "城东物流园换电站", 15.0, 100.0, timedelta(days=1, hours=1)),
    ("沪EV7788", "高新园区换电站", 20.0, 100.0, timedelta(minutes=40)),
]


def init_db() -> None:
    """创建所有表并按稳定业务标识补齐种子数据（可重复、可并发调用）。"""
    database.Base.metadata.create_all(bind=database.engine)
    # 两个进程/线程同时初始化时，可能撞上唯一约束或 SQLite 写锁：
    # 本事务整体回滚后重试一次即可看到对方已提交的数据，继而只补真正缺失的行。
    for attempt in range(2):
        db = database.SessionLocal()
        try:
            _seed_all(db)
            db.commit()
            return
        except (IntegrityError, OperationalError) as exc:
            db.rollback()
            if attempt == 0 and _is_concurrent_conflict(exc):
                continue
            raise
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()


def _is_concurrent_conflict(exc: Exception) -> bool:
    """唯一约束冲突或 SQLite 写锁争抢都视为可重试的并发初始化冲突。"""
    if isinstance(exc, IntegrityError):
        return True
    message = str(exc.orig if isinstance(exc, OperationalError) else exc).lower()
    return "locked" in message or "busy" in message


def _seed_all(db) -> None:
    _seed_admin(db)
    _seed_stations(db)
    _seed_vehicles(db)
    _seed_swaps(db)


def _seed_admin(db) -> None:
    exists = db.query(User).filter(User.username == DEFAULT_ADMIN_USERNAME).first()
    if not exists:
        db.add(
            User(
                username=DEFAULT_ADMIN_USERNAME,
                password_hash=hash_password(DEFAULT_ADMIN_PASSWORD),
                display_name="平台管理员",
            )
        )


def _seed_stations(db) -> None:
    # 按稳定业务标识（站名）逐条检查，而不是看表里有没有任意一行。
    existing = {station.name for station in db.query(Station).all()}
    for seed in STATION_SEEDS:
        if seed["name"] not in existing:
            db.add(Station(**seed))
    db.flush()


def _seed_vehicles(db) -> None:
    # 按稳定业务标识（车牌）逐条检查。
    existing = {vehicle.plate for vehicle in db.query(Vehicle).all()}
    for seed in VEHICLE_SEEDS:
        if seed["plate"] not in existing:
            db.add(Vehicle(**seed))
    db.flush()


def _seed_swaps(db) -> None:
    vehicles_by_plate = {v.plate: v for v in db.query(Vehicle).all()}
    stations_by_name = {s.name: s for s in db.query(Station).all()}

    # 换电记录没有独立业务编码，用“车 + 站 + 换电前后电量”作为稳定业务标识。
    rows = (
        db.query(SwapRecord, Vehicle, Station)
        .join(Vehicle, SwapRecord.vehicle_id == Vehicle.id)
        .join(Station, SwapRecord.station_id == Station.id)
        .all()
    )
    existing = {
        (vehicle.plate, station.name, record.soc_before, record.soc_after)
        for record, vehicle, station in rows
    }

    for plate, station_name, soc_before, soc_after, offset in SWAP_SEEDS:
        vehicle = vehicles_by_plate.get(plate)
        station = stations_by_name.get(station_name)
        if vehicle is None or station is None:
            continue
        key = (plate, station_name, soc_before, soc_after)
        if key not in existing:
            db.add(
                SwapRecord(
                    vehicle_id=vehicle.id,
                    station_id=station.id,
                    soc_before=soc_before,
                    soc_after=soc_after,
                    swapped_at=_SEED_BASE_TIME - offset,
                )
            )
