# 换电站运营管理平台（纯后端）

新能源物流车换电站后台管理的纯后端 API 服务，提供站点、车辆和换电记录的统一管理能力。

## 技术栈

- FastAPI + Uvicorn
- SQLAlchemy + SQLite（本地文件，开箱即用）
- PyJWT（JWT 鉴权）
- 密码哈希用标准库 `hashlib.pbkdf2_hmac`，无额外依赖

所有数据本地、离线可运行，不依赖任何外部服务。

## 运行

```bash
pip install -r requirements.txt
python run.py
```

服务启动在 `http://127.0.0.1:7634`，首次启动自动建表并灌入种子数据。
交互式文档：`http://127.0.0.1:7634/docs`。

数据库文件默认是项目根目录的 `data.db`，可用环境变量覆盖：

- `APP_DB_PATH`：自定义数据库文件路径
- `APP_DATABASE_URL`：直接指定完整 SQLAlchemy 连接串（优先级更高）

种子数据按稳定业务标识（管理员用户名、站点名称、车牌、车+站+换电前后电量）
在单个事务中逐条补齐：重复启动或多进程并发初始化都不会重复插入，
已有站点及其配置保持原样，仅补回缺失的车辆与换电记录。

## 内置账号

首次启动自动创建唯一管理员（本平台只有 admin 一个角色）：

- 用户名：`admin`
- 密码：`admin123`

## 已实现的基础功能

- 登录签发 JWT、获取当前用户（`/api/auth/login`、`/api/auth/me`）
- 换电站增删改查（`/api/stations`）
- 车辆增删改查（`/api/vehicles`）
- 换电记录查询与登记（`/api/swaps`，会联动更新车辆电量与站点可用电池）
- 仪表盘统计（`/api/dashboard/stats`）
- 健康检查（`/api/health`）

除 `login` 与 `health` 外，所有接口均需携带 `Authorization: Bearer <token>`。

## 测试

```bash
pip install -r requirements.txt
pytest -q
```

测试会为每个用例创建独立的临时数据库（`tmp_path`），用例间状态可预测，
连续多次运行结果稳定，并且始终不会读取或清理生产用的 `data.db`。

## 编码说明

源码与数据均为 UTF-8；FastAPI 响应为 UTF-8 JSON，中文不转义、不乱码。
Windows 控制台若为 GBK，仅影响终端打印观感，不影响接口返回。
