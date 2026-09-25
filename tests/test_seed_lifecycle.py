"""种子数据与数据库生命周期的回归测试。

覆盖本次修复的核心场景：
- 重复初始化不产生重复数据（按稳定业务标识，而非行数判断）；
- 从不完整备份恢复（只剩站点表）后，缺失的车辆与换电记录被补齐，
  已有站点及其配置保持原样；
- 两个进程竞争初始化不会重复插入；
- 测试运行在隔离的临时数据库上，不会触碰生产 data.db。
"""
import os
import subprocess
import sys
import tempfile
from pathlib import Path

from app.config import BASE_DIR, DB_PATH
from app.database import Base, SessionLocal, engine
from app.models import Station, SwapRecord, User, Vehicle
from app.seed import init_db

PROJECT_ROOT = Path(BASE_DIR)


def _counts():
    db = SessionLocal()
    try:
        return {
            "users": db.query(User).count(),
            "stations": db.query(Station).count(),
            "vehicles": db.query(Vehicle).count(),
            "swaps": db.query(SwapRecord).count(),
        }
    finally:
        db.close()


def test_uses_isolated_temp_database_not_production_file():
    # 测试必须使用系统临时目录中的隔离库，而不是项目根目录的生产数据
    assert Path(DB_PATH) != PROJECT_ROOT / "data.db"
    assert str(Path(DB_PATH).resolve()).startswith(str(Path(tempfile.gettempdir()).resolve()))
    assert not (PROJECT_ROOT / "data.db").exists()


def test_repeated_init_does_not_duplicate_seeds():
    expected = {"users": 1, "stations": 4, "vehicles": 5, "swaps": 4}
    assert _counts() == expected
    for _ in range(3):
        init_db()
    assert _counts() == expected

    db = SessionLocal()
    try:
        # 稳定标识唯一：车牌、站名不应有重复
        assert db.query(Vehicle.plate).distinct().count() == db.query(Vehicle).count()
        assert db.query(Station.name).distinct().count() == db.query(Station).count() == 4
        # 每条换电记录的业务键（车辆+站点+时间）也不应重复
        keys = {
            (r.vehicle_id, r.station_id, r.swapped_at.isoformat())
            for r in db.query(SwapRecord).all()
        }
        assert len(keys) == 4
    finally:
        db.close()


def test_partial_restore_refills_missing_vehicles_and_swaps_keeps_stations():
    """模拟从不完整备份恢复：只剩站点表，且站点配置已被运营修改过。"""
    db = SessionLocal()
    try:
        station = db.query(Station).filter(Station.name == "城东物流园换电站").first()
        station.battery_ready = 7  # 运营中已变化的配置
        station.status = "maintenance"
        db.query(SwapRecord).delete()
        db.query(Vehicle).delete()
        db.commit()
    finally:
        db.close()

    assert _counts() == {"users": 1, "stations": 4, "vehicles": 0, "swaps": 0}

    init_db()  # 再次启动：应补齐缺失数据，且不动已有站点

    assert _counts() == {"users": 1, "stations": 4, "vehicles": 5, "swaps": 4}
    db = SessionLocal()
    try:
        station = db.query(Station).filter(Station.name == "城东物流园换电站").first()
        # 已有站点及其配置必须保持恢复后的原样，不能被种子值覆盖
        assert station.battery_ready == 7
        assert station.status == "maintenance"
        plates = {v.plate for v in db.query(Vehicle).all()}
        assert plates == {"沪EV1234", "沪EV5678", "苏EV9012", "浙EV3456", "沪EV7788"}
    finally:
        db.close()


def test_partial_missing_rows_are_filled_not_masked_by_table_nonempty():
    """表非空但只缺一部分时（不能用行数掩盖部分缺失），缺什么补什么。"""
    db = SessionLocal()
    try:
        # 只保留 2 辆车，并清空换电记录
        keep = {"沪EV1234", "沪EV5678"}
        for vehicle in db.query(Vehicle).all():
            if vehicle.plate not in keep:
                db.delete(vehicle)
        db.query(SwapRecord).delete()
        db.commit()
    finally:
        db.close()

    init_db()

    assert _counts() == {"users": 1, "stations": 4, "vehicles": 5, "swaps": 4}


def test_concurrent_process_init_has_no_duplicates():
    """两个进程同时初始化空库：建表 + 种子竞争，最终也不能有重复项。"""
    Base.metadata.drop_all(bind=engine)
    engine.dispose()  # 放开文件句柄，避免干扰跨进程锁

    code = "from app.seed import init_db, dispose; init_db(); dispose()"
    env = dict(os.environ)  # DB_PATH 指向当前隔离临时库，子进程继承
    procs = [
        subprocess.Popen([sys.executable, "-c", code], cwd=str(PROJECT_ROOT), env=env)
        for _ in range(2)
    ]
    for proc in procs:
        assert proc.wait(timeout=60) == 0

    # 重新建引擎连接（dispose 后可透明重建）
    assert _counts() == {"users": 1, "stations": 4, "vehicles": 5, "swaps": 4}
    db = SessionLocal()
    try:
        assert db.query(Vehicle.plate).distinct().count() == 5
        assert db.query(Station.name).distinct().count() == 4
        assert db.query(SwapRecord).count() == 4
    finally:
        db.close()
