# 证据目录

本目录存放被报告引用的机器可读证据（JSON / JSONL），随代码提交，
用于保证报告中的结论可追溯到具体运行。

约定：

- 原始运行日志（`stage1_sim.log`、`step1_1.log`、`coppelia_console*.log` 等）
  不提交；`.gitignore` 已排除。
- 运行脚本默认把输出写入 `docs/implementation/runs/`（同样不提交）。
- 只有报告或验收记录实际引用的文件才复制到本目录并提交；
  文件名保留原始时间戳，不覆盖历史证据。
- 当前阶段一主布局 `L1_S1` 的运行证据：`phase1_*_20261020_163*.json`。
- suction pad 安装修正（2026-10-20）：`step1_1_layout_20261020_221308.json`、
  `phase1_run_*_20261020_221*.json`、`phase1_run_agv_agv_blocked_20261020_222105.json`、
  `step1_1_experiments_20261020_222346.json`、`step1_1_trials_B_20261020_222346.jsonl`、
  `tool_fault_injection_20261020_222250.json`。
- 步骤 1.1 历史证据：`step1_1_*_20261020_104655.*`。
- 迁移前的阶段一基线证据：`phase1_*`（无时间戳及 `_20261020_110827`）。
- 步骤 1.2 场景生命周期（2026-10-20/21）：
  - `step1_2_lifecycle_checks_20261020_235739.json` — 负向与并发检查
    （alias 移除、配置摘要不匹配、运行中 load/build 拒绝、基线哈希不变）；
  - `step1_2_post_restart_reuse_20261020_234207.json` — 第一次 CoppeliaSim
    进程重启后加载 v001、130 项校验通过并完成 station 任务；
  - `step1_2_post_restart_reuse_v002_20261021_000357.json` — 第二次重启后
    对最终基线 v002 的加载/校验/运行；
  - `step1_2_post_restart_run_*.json` — 对应的完整运行报告（含物料/AGV/
    传送带证据）；
  - `step1_2_deferred_items_20261021_091500.json` — 暂缓项补齐：UR5 关节
    模式、`--keep-running` 后台/回收、死亡 PID 锁清理、alias 索引性能。
