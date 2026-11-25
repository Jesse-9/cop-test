---
title: v1 订单运行路径复用接入方案
type: plan
status: implemented
created: 2026-11-24
updated: 2026-11-24
scope: v1 UFLOW / order run / motion path reuse / API / CLI / frontend start dialog
related:
  - v1-order-variant-catalog-workbench.md
  - v1-order-dag-phase-09-scene-profile-order-catalog.md
  - v1-order-dag-phase-08-fleet-aware-planner.md
  - ../../reports/simulation/v1-all-arm-path-reuse.md
  - ../../reports/order-orchestration/v1-order-run-path-reuse.md
  - ../../evidence/order-orchestration/v1_order_run_path_reuse_20261124.json
  - ../../reports/order-orchestration/v1-order-variant-catalog-workbench.md
  - ../../../wiki/98-ProjectUsage/USAGE.md
---

# v1 订单运行路径复用接入方案

> 实施结果（2026-11-24）：已接入订单 API/CLI/前端启动对话框；
> 后端 793 passed / 8 skipped，前端 16 passed，E2E 6 passed。详见
> [实施报告](../../reports/order-orchestration/v1-order-run-path-reuse.md)
> 与[证据](../../evidence/order-orchestration/v1_order_run_path_reuse_20261124.json)。

## 1. 目标与结论

把已有的固定动作关节路径复用（`PLAN_ONLY` / `CACHE_ONLY` /
`CACHE_THEN_PLAN`）接入订单 DAG 的**运行链路**，让前端订单启动、订单 HTTP
API 和 `order start` CLI 都能使用 `docs/path_library/` 中已通过最终复核的
五臂 `PLACING` 路径记录。

结论：**不新增缓存算法，不改变路径记录格式，不做数据库迁移。** 只补齐
`OrderRunCommand` → `OrderRunnerConfig` → `Stage4Coordinator` 之间缺失的
策略传递，并把运行选项暴露到 API、CLI 和前端启动对话框。

## 2. 现状与问题

路径复用自阶段 7/8 起只在 `flexible-factory run` 的生命周期 runner 中
构造：

- `application/run.py:build_path_reuse_policy()` 负责模式校验、路径库根
  目录校验和 `CACHE_ONLY` 非空预检；
- `run_loaded_scene_v1()` 把 `PathReusePolicy` 传给
  `Stage4Coordinator(path_reuse=...)`；
- CLI 只在 `run` 子命令注册 `--path-reuse` / `--path-library-root`。

订单运行链路完全没有接入：

| 位置 | 现状 |
|---|---|
| `OrderRunCommand` | 只有订单、工件、车队等字段，没有路径复用模式 |
| `OrderService.start()` / `run_sync()` | 不接受路径复用参数 |
| `POST /api/orders/{id}/versions/{v}/start` | 只接收 `artifact_id`、`max_sim_time`、`transport_fleet` |
| `OrderRunnerConfig` | 没有路径库根目录 |
| `order_runner.run_order_once()` | 构造 `Stage4Coordinator` 时没有 `path_reuse` |
| 前端启动对话框 | 没有路径复用选项 |

因此从前端/API 启动的订单始终在线规划所有 `PLACING` 段，`docs/path_library`
中的 7 条 v005 记录不会被查询。CLI `run` 实测 `CACHE_THEN_PLAN` 为
9 次 lookup → 7 次 replay + 2 次回退，lookup 0.015 s、planner 12.953 s；
订单链路当前没有这部分收益。

## 3. 最小设计

### 3.1 运行命令

`OrderRunCommand` 增加：

```python
path_reuse_mode: str = "off"   # off | PLAN_ONLY | CACHE_ONLY | CACHE_THEN_PLAN
```

`as_dict()` 同步记录该字段，运行报告可追溯。模式不进入 `OrderSpec`、订单
版本、DAG digest 或运行记录数据库 schema；它是运行选项，语义与
`transport_fleet` 相同。

### 3.2 服务校验

`OrderService.start()` 与 `run_sync()` 增加 `path_reuse: str = "off"` 参数，
在创建 run 记录前用 `PATH_REUSE_MODES` 校验；非法值抛
`OrderRunConfigError`，HTTP 层映射为 422 `ORDER_RUN_CONFIG`。

不在服务层预检路径记录数量：`CACHE_ONLY` 的记录缺失由 runner 构造策略时
拒绝，运行进入 `FAILED` 并保留错误信息；推荐使用 `CACHE_THEN_PLAN`，未命中
自动回退在线规划。

### 3.3 Runner

`OrderRunnerConfig` 增加：

```python
path_library_root: Optional[Path] = None   # None 时使用 spec.PATH_LIBRARY_ROOT
```

`run_order_once()` 在连接 CoppeliaSim 前：

```python
reuse = build_path_reuse_policy(
    command.path_reuse_mode, config.path_library_root, command.artifact_id)
```

并把 `reuse` 传给 `Stage4Coordinator(..., path_reuse=reuse)`。运行结束后把
`path_reuse_info()` 与 `policy.stats()` 合并写入
`summary["path_reuse"]`，包含：

- `mode` / `library_root` / `scene` / `record_count`；
- `lookup` / `replay` / `fallback` / `planner_calls`；
- `lookup_wall_s` / `planner_wall_s` / `by_unit`；
- `record_count_after`。

`off` 时 `summary["path_reuse"] = None`，不改变原有规划行为。

### 3.4 API

`POST /api/orders/{order_id}/versions/{version}/start` 的 JSON body 增加
可选 `path_reuse`。优先级：

```text
body.path_reuse > api serve --path-reuse 默认值 > "off"
```

路径库根目录只由服务端配置（`api serve --path-library-root`），不接受
客户端提交任意文件系统路径。`/api/health` 增加
`path_reuse_modes` 与 `default_path_reuse`，供前端和运维发现当前默认值。

### 3.5 CLI

- `flexible-factory api serve` 增加 `--path-reuse` 和
  `--path-library-root`，默认分别为 `off` 和 `docs/path_library`；
- `flexible-factory order start` 增加同名参数，默认 `off`；
- `order start` 与 `api serve` 的 runner 共享 `OrderRunnerConfig` 的
  路径库根目录。

### 3.6 前端

`StartRunDialog` 增加“路径复用”下拉框：

| 选项 | 含义 |
|---|---|
| `CACHE_THEN_PLAN`（默认） | 先查缓存，未命中自动在线规划并执行 |
| `off` | 关闭，保持原有在线规划 |
| `CACHE_ONLY` | 只回放；没有匹配记录时启动失败 |
| `PLAN_ONLY` | 只在线规划并导出记录，用于生成缓存 |

创建请求增加 `path_reuse`。对话框提示：只覆盖五台臂的 `PLACING` 段；
`docs/path_library` 当前只有 v005 的记录，v002/MVP 需先用 `PLAN_ONLY`
生成。

## 4. 运行时行为

- 路径复用策略仍只对 `enabled_phases=("PLACING",)` 生效，五臂全部启用；
- 键仍为场景工件 + 动作阶段 + 工位 + 机器人 + 工具 + 负载 + 起始关节摘要
  + 目标摘要 + 运动配置版本；
- 执行期碰撞、偏差、超时和腕部分支联锁不因回放而跳过；
- `CACHE_ONLY` 未命中返回 `PATH_REPLAY_MISS`，不自动规划；
- `CACHE_THEN_PLAN` 未命中回退到原在线规划器；
- `PLAN_ONLY` 不查缓存，在线规划成功后按 `export=True` 写记录。

## 5. 测试与验收

后端：

- `OrderService.start()`/`run_sync()` 把模式写入 `OrderRunCommand`；
- 非法模式在创建 run 前返回 `OrderRunConfigError`，不写 run 记录；
- HTTP start body 的 `path_reuse` 覆盖 `api serve` 默认值；
- body 缺省时使用 server 默认值；
- `/api/health` 暴露模式列表和默认值；
- 现有路径复用策略/工件测试保持通过。

前端：

- Vitest/typecheck/build 保持通过；
- Playwright 启动订单旅程断言 start 请求携带 `path_reuse`；
- 既有订单、方案目录、运行视图旅程不回归。

验收命令：

```text
uv run pytest -q
cd frontend && npm test
cd frontend && npm run e2e
cd frontend && npm run typecheck
cd frontend && npm run build
```

本轮不新增 CoppeliaSim 实机验收；运行链路的物理正确性沿用阶段 7/9
订单验收和路径复用 focused 证据。若需要证明前端订单实际命中缓存，应在
CoppeliaSim 可用时单独执行一次带 `path_reuse` 的订单运行，并保存运行
报告中的 `path_reuse` 统计。

## 6. 边界与未覆盖

- 不做跨工件路径迁移、托盘相对路径变换或路径平移；
- 不把 `PLACING` 之外的 `PICKING`、AGV 运输、home 等段纳入缓存；
- 不新增路径记录版本、数据库表/列、`OrderSpec` 字段或 planner 策略版本；
- 不允许前端指定路径库根目录；
- 不把 `CACHE_THEN_PLAN` 的命中宣传为物理可行性验证；执行期检查不变。

## 7. 实施顺序

1. `OrderRunCommand` / `OrderService` / runner 接线；
2. API body、server 默认值、health 字段；
3. CLI `api serve` / `order start` 参数；
4. 前端启动对话框与请求字段；
5. 后端/前端测试；
6. 实施报告、证据、索引、工程参考和使用指南。