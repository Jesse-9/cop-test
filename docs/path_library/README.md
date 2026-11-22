# 固定动作路径库

本目录保存已通过现有规划器最终复核的固定动作关节路径，供
`PathReusePolicy` 在**场景/机器人/工具/载荷/起点/目标/运动配置版本**完全
一致时回放。它不是场景工件，不包含 CoppeliaSim 句柄、运行期 context 或
会话对象；删除单个记录只会让对应动作回退到在线规划。

## 目录与版本

```text
docs/path_library/<scene-slug>/<phase-slug>/<path-id>.json
```

- 记录版本：`v1_path_record_20261121`（由 `path_library.py` 定义）。
- 策略版本：`v1_path_reuse_20261121`（写入 `plan.replay`，不改变工件）。
- `<scene-slug>` 来自场景工件 ID，例如
  `v1/L5_UFLOW/v005_20261117_110924` → `v1_L5_UFLOW_v005_20261117_110924`。
- `<phase-slug>` 当前为 `PLACING_place`。
- `<path-id>` 是精确匹配键的 SHA-256 前缀。

## 生成

在目标场景工件上运行一次 `full_flow`，让五臂 `PLACING` 走现有规划器并
导出成功计划（不改速度/碰撞/执行参数）：

```text
flexible-factory run --artifact v1/L5_UFLOW/v005_20261117_110924 \
  --scenario full_flow --path-reuse PLAN_ONLY \
  --path-library-root docs/path_library --run-records off \
  --out-dir docs/implementation/runs \
  --log docs/implementation/runs/path_reuse_plan_only.log
```

只保存 `MotionPlan.ok` 且通过最终复核、段点数与 waypoint 总数一致的记录。
若某个动作变体未在本次 `full_flow` 出现，用对应 scenario/focused harness
补齐；不要手工伪造记录。

## 使用

```text
flexible-factory run --artifact v1/L5_UFLOW/v005_20261117_110924 \
  --scenario full_flow --path-reuse CACHE_THEN_PLAN \
  --path-library-root docs/path_library --run-records off
```

- 默认 `--path-reuse off`。
- `CACHE_THEN_PLAN`：先精确回放，未命中调用一次现有规划器；用于日常运行。
- `CACHE_ONLY`：只回放，未命中失败；用于固定动作回归。五臂全启时 B/D
  的 AGV 托盘位姿变化会按设计未命中，不应把该模式当作全臂生产默认。
- `PLAN_ONLY`：始终在线规划并导出（仅在该模式下写记录）。

## 失效与限制

- 任一 key 字段变化即未命中：场景工件、臂、工具杯口半径、载荷、起始
  关节、目标摘要、运动配置版本。
- B/D 的 `PLACING` 目标是 AGV 实测托盘位姿；托盘有厘米级变化时精确记录
  不会命中，`CACHE_THEN_PLAN` 自动回退。本次不保存托盘相对路径，也不
  做路径平移/局部修复。
- 回放仍经过原 `JointStreamExecutor` 的逐步碰撞、关节偏差、超时和腕部
  分支联锁；记录不得包含句柄或旧运行期状态。
- 路径库根目录不得位于 `docs/scene_artifacts/`。

## 验证

```text
python -m pytest -q tests/test_v1_path_library_artifacts.py
```

该测试逐条加载本目录 JSON，校验版本、段点数、有限数值和自洽性，并断言
A–E 五臂均有 `PLACING` 记录。