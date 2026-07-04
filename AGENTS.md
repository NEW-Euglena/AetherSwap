# AetherSwap 项目指南

本文件面向后续进入本仓库的 Agent。先读这里，再按任务需要深入具体代码，避免每次都从零开始扫描项目。

## 项目定位

AetherSwap 是一个本地运行的 Steam 饰品低价余额助手和 Web 控制台。项目使用 Python 3.10+、FastAPI、SQLModel/SQLite、Playwright，以及原生 HTML/JS/CSS。

核心能力包括：

- Steam 饰品低价余额流程：选品、采买、入库、上架、确认、交易记录沉淀。
- Buff 采买对接，SteamDT/iflow 行情拉取与筛选。
- Steam 库存、市场挂单、出售价格、币种换算、Steam Guard 确认。
- 交易记录、持仓分析、盈亏统计、日志查看、代理池管理。
- Steam 折扣游戏抓取、排序和折扣分享卡生成。

## 运行与验证

常用命令：

```bash
pip install -r requirements.txt
python -m playwright install chromium
python run.py
pytest tests/ -v
```

按改动范围优先跑最小测试集，例如：

```bash
pytest tests/test_pipeline_steps.py -v
pytest tests/test_config_schema.py -v
pytest tests/test_market_orders.py -v
pytest tests/test_sell_pipeline_currency_guard.py -v
```

文档或纯前端静态文案改动通常不需要启动应用；后端流程、配置、数据库、支付状态、上架价格相关改动必须至少跑相关单元测试。

## 目录地图

- `run.py`：项目根目录启动入口，启动 FastAPI 服务并打开本地 Web 控制台。
- `app/api.py`：FastAPI 应用入口，初始化数据库、迁移旧 JSON 数据、启动后台 worker、注册路由。
- `app/routes/`：API 路由层，包含状态、交易、pipeline、库存、认证、账号、配置、代理、Steam 折扣、静态文件等路由。
- `app/pipeline.py`、`app/pipeline_steps.py`、`app/sell_pipeline.py`：采买、支付确认、待收货记录、出售上架、自动确认等主流程。
- `app/services/`：后台任务、任务队列，以及 Buff、Steam、Steam 折扣、分享卡等服务封装。
- `app/database.py`：SQLModel/SQLite 数据模型和交易、Steam 折扣游戏、item_nameid 等数据库操作。
- `app/config_schema.py`、`app/config_loader.py`、`config/__init__.py`：配置默认值、校验、缓存和本地 JSON 读写。
- `app/state.py`：运行态状态、日志、支付确认、库存快照和停止信号。
- `steam/`、`buff/`、`steamdt/`：第三方平台请求、解析和自动化封装。
- `analysis/`、`utils/`：稳定性分析、价格趋势、金额换算、代理、限流、时间和网络工具。
- `web/`：原生前端，`web/index.html` 是主页面，`web/js/` 按功能拆分，`web/css/` 存放样式。
- `tests/`：pytest 单元测试，覆盖配置、pipeline、支付状态、代理池、Steam 订单簿、数据库和出售保护逻辑。

## 关键流程

采买流程大致为：

```text
SteamDT/iflow 行情 -> 折扣与关键词过滤 -> 稳定性分析 -> 平台价格校验 -> Buff 下单 -> 支付/收货状态处理 -> 写入采购记录
```

出售流程大致为：

```text
Steam 库存监听 -> 识别可出售饰品 -> 计算智能上架价 -> Steam 市场上架 -> Steam Guard 确认 -> 写入出售与持仓状态
```

后台 worker 由 `app/services/task_queue.py` 调度，主要在 `app/services/workers.py` 中运行，负责收货检查、上架检查、汇率刷新、持仓报告、账号区域同步和 session 保活。

## 数据、配置与安全边界

本项目会在本地保存敏感信息和运行态数据，修改时必须小心：

- `config/credentials.json`：Steam/Buff Cookie 等凭据。
- `config/accounts.json`：本地账号信息。
- `config/app_config.json`：用户配置。
- `config/app.db` 及 `app.db-*`：SQLite 数据库。
- `config/playwright_steam/`、`config/playwright_buff/`：Playwright 登录态。
- `config/exchange_rate.json`、`config/steam_userdata.json`、`config/holdings_report_last.json`：运行缓存。
- `log/`：运行日志。

不要提交、展示、复制或重置上述敏感文件。除非用户明确要求，不要删除数据库、凭据、登录态、日志或配置文件。

## 开发约定

- 所有对用户的沟通、文档、新增注释和新增 docstring 使用简体中文。
- 保持外科手术式修改：只改任务需要的文件，不做无关重构、格式化或“顺手优化”。
- 优先沿用现有模块边界和风格：路由放 `app/routes/`，平台封装放 `app/services/` 或对应平台目录，通用逻辑放 `utils/`。
- 涉及业务逻辑、金额、币种、支付、库存、上架、配置校验时，优先补或更新最小测试，再让测试通过。
- 不要绕过现有保护逻辑，例如 pipeline 并发锁、请求限流、代理池评分、币种汇率保护、Steam 后台任务插队限制。
- 修改前注意工作区可能已有用户改动。不要回滚、覆盖或清理不是自己造成的改动。
- 前端是无构建步骤的原生页面，修改 `web/index.html`、`web/js/`、`web/css/` 时保持现有文件组织和 API 调用方式。

## 常见入口

- 启动/停止 pipeline：`app/routes/pipeline.py` -> `app/pipeline.py`。
- 状态与日志：`app/routes/status.py` -> `app/state.py`。
- 交易记录：`app/routes/transactions.py` -> `app/database.py`。
- 配置页面/API：`app/routes/config.py` -> `app/config_loader.py` -> `app/config_schema.py`。
- 库存与市场价：`app/routes/inventory.py`、`steam/inventory.py`、`steam/market_orders.py`。
- Buff 支付状态：`buff/buyer.py`、`app/pipeline_steps.py`。
- Steam 折扣游戏：`app/routes/steam_deals.py`、`app/services/steam_deals.py`、`app/services/deal_cards.py`。
