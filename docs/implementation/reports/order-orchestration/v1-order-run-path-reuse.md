---
title: 订单运行路径复用接入实施报告
type: report
status: review
created: 2026-11-24
updated: 2026-11-24
scope: v1 UFLOW / order run / motion path reuse / API / CLI / frontend start dialog
related:
  - ../../plans/order-orchestration/v1-order-run-path-reuse.md
  - ../../plans/order-orchestration/v1-order-variant-catalog-workbench.md
  - ../../reports/simulation/v1-all-arm-path-reuse.md
  - ../../evidence/order-orchestration/v1_order_run_path_reuse_20261124.json
  - ../../../wiki/98-ProjectUsage/USAGE.md
---

# 订单运行路径复用接入实施报告

本报告记录
[订单运行路径复用接入方案](../../plans/order-orchestration/v1-order-run-path-reuse.md)
的实施结果：把已有的五臂 `PLACING` 固定路径复用从 `run` 生命周期接入
订单 DAG 运行链路，使前端订单启动、订单 HTTP API 和 `order start` CLI
都能选择 `off` / `PLAN_ONLY` / `CACHE_ONLY` / `CACHE_THEN_PLAN`。

结论：**已实施**。不新增缓存算法、路径记录格式或数据库表/列；只补齐
运行选项的传递和暴露。前端启动对话框默认 `CACHE_THEN_PLAN`，未命中自动
回退在线规划；`api serve` 与 `order start` 默认仍为 `off`，保持原有行为。

## 1. 接入点

| 入口 | 默认 | 覆盖方式 |
|---|---|---|
| 前端 `StartRunDialog` | `CACHE_THEN_PLAN` | 下拉框选择，随 `POST .../start` body 发送 |
| `POST /api/orders/{id}/versions/{v}/start` | `api serve --path-reuse` | body `path_reuse` 覆盖服务端默认值 |
| `flexible-factory api serve` | `off` | `--path-reuse` / `--path-library-root` |
| `flexible-factory order start` | `off` | `--path-reuse` / `--path-library-root` |
| `flexible-factory run` | `off` | 原有 `--path-reuse` / `--path-library-root` |

路径库根目录只由服务端配置；前端/API 客户端只提交模式，不提交任意文件
系统路径。

## 2. 代码改动

- `application/orders.py`
  - `OrderRunCommand` 增加 `path_reuse_mode: str = "off"`，`as_dict()` 记录；
  - `OrderService.normalize_path_reuse()` 在创建 run 前校验模式；
  - `OrderService.start()` / `run_sync()` 增加 `path_reuse` 参数，写入命令；
  - 非法模式抛 `OrderRunConfigError`，HTTP 映射为 422
    `ORDER_RUN_CONFIG`，不产生 run 记录。
- `application/order_runner.py`
  - `OrderRunnerConfig` 增加 `path_library_root`；
  - 新增 `build_order_path_reuse()`，用 `command.path_reuse_mode`、
    服务端路径库根目录和 `command.artifact_id` 构造策略；
  - `run_order_once()` 在连接 CoppeliaSim 前构造策略，传给
    `Stage4Coordinator(path_reuse=...)`；
  - 运行结束把 `path_reuse_info()` 与 `policy.stats()` 合并写入
    `summary["path_reuse"]`，额外记录 `record_count_after`；
  - `off` 时该字段为 `null`，不改变原在线规划行为。
- `api/server.py`
  - `OrderApiServer` 增加 `default_path_reuse`；
  - start body 支持 `path_reuse`，优先级为 body > 服务默认 > `off`；
  - `/api/health` 增加 `path_reuse_modes` 与 `default_path_reuse`。
- `cli.py`
  - `api serve` 与 `order start` 注册 `--path-reuse` /
    `--path-library-root`；
  - runner 配置接收路径库根目录；`api serve` 把默认模式传给 API server。
- 前端
  - `StartRunBody` 增加 `path_reuse`；
  - `StartRunDialog` 增加“路径复用”下拉框，默认 `CACHE_THEN_PLAN`；
  - 提示只覆盖五臂 `PLACING`；v005 已有记录，v002/MVP 需先 `PLAN_ONLY`
    生成。

## 3. 运行时行为

- 策略仍只对 `enabled_phases=("PLACING",)` 生效，五臂全部启用；
- 匹配键、记录版本、执行期碰撞/偏差/超时/腕部分支联锁均不变；
- `CACHE_THEN_PLAN` 未命中时调用一次现有规划器；
- `CACHE_ONLY` 未命中返回 `PATH_REPLAY_MISS`，不自动规划；
- `PLAN_ONLY` 始终在线规划并按策略导出记录；
- `path_reuse` 不进入 `OrderSpec`、订单版本、DAG digest 或订单数据库
  schema，语义与 `transport_fleet` 相同，属于运行配置。

## 4. 测试与验证

执行日期：2026-11-24。

| 命令 | 结果 |
|---|---|
| `uv run pytest -q` | **793 passed, 8 skipped** |
| `cd frontend && npm test` | **16 passed** |
| `cd frontend && npm run e2e` | **6 passed** |
| `npm run typecheck` | 通过 |
| `npm run build` | 通过 |

新增/扩展覆盖：

- 服务层：默认 `off`、显式 `CACHE_THEN_PLAN` 写入 `OrderRunCommand`、
  非法模式在创建 run 前拒绝；
- API：body 缺省使用 `api serve` 默认值、body 覆盖默认值、非法值返回
  422、health 暴露模式列表和默认值；
- runner 单元：`off` 返回 `None`、`CACHE_THEN_PLAN` 构造带场景/PLACING
  范围的策略、未知模式和空库 `CACHE_ONLY` 拒绝；
- 前端 E2E：订单详情启动对话框默认 `CACHE_THEN_PLAN`，切换到
  `CACHE_ONLY` 后 start 请求携带对应字段并跳转运行页。

本轮未启动 CoppeliaSim，因此**没有**新增订单运行命中缓存的实机证据。
现有路径复用实机证据仍是
[五臂路径回放扩展报告](../../reports/simulation/v1-all-arm-path-reuse.md)；
本轮 E2E 只验证前端请求字段，不代表实际回放或物理可行性。

## 5. 版本与数据

- 复用 `v1_path_reuse_20261121` 与 `v1_path_record_20261121`，不新增
  路径记录版本；
- `ORDER_CONTRACT_VERSION`、`PLANNER_POLICY_VERSION`、
  `SCENE_PROFILE_VERSION`、`PROCESS_MODEL_VERSION` 均不变；
- 无数据库迁移；`path_reuse` 只出现在运行命令、运行报告和
  `/api/health`；
- `docs/path_library` 仍只有 `v005_20261117_110924` 的 7 条
  `PLACING_place` 记录；v002/MVP 没有记录，`CACHE_THEN_PLAN` 会全部回退
  在线规划。

## 6. 边界与未覆盖

- 只覆盖五臂 `PLACING` 单目标动作；`PICKING`、AGV 运输、home 等仍在线
  规划；
- 不做托盘相对路径变换、路径平移或跨工件迁移；
- 不允许前端指定路径库根目录；
- `CACHE_ONLY` 缺记录时运行进入 `FAILED`，不是启动前预检；推荐
  `CACHE_THEN_PLAN`；
- 未重新执行订单级 CoppeliaSim 验收；若需证明订单运行实际命中缓存，
  应在 CoppeliaSim 可用时执行一次带 `path_reuse` 的订单运行并保存运行
  报告中的 `path_reuse` 统计。

证据：[v1_order_run_path_reuse_20261124.json](../../evidence/order-orchestration/v1_order_run_path_reuse_20261124.json)。