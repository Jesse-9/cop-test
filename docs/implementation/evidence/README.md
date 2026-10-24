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

- 阶段二物理约束与运动规划（2026-10-21，工件
  `stage2/L1_S1/v009_20261021_190527`）：
  - `phase2_run_station_none_20261021_190559.json` — 正常 station：
    DONE，物料位置误差 0.00062 m；
  - `phase2_run_agv_none_20261021_190741.json` — 正常 agv：载荷在载台，
    轮速行驶 0.5022 m，跟随误差 0.0087 m；
  - `phase2_run_both_none_20261021_190928.json` — station+agv 两任务
    DONE（UID 1/2），无串任务；
  - `phase2_run_station_runtime_obstacle_20261021_191237.json` — 运行期
    受控障碍：`SAFETY_LATCH: COLLISION`，对象对
    `link3_visible <-> Fault_Runtime_Obstacle`，停止确认 4e-5 m/s；
  - `stage2_path_rejection_20261021_121749.json` — 两端安全、中段
    `PATH_COLLISION`（`Body <-> Fault_Runtime_Obstacle`），移除后恢复；
  - `stage2_inventory_20261021_104837.json` — P2-0 真实几何/4.10 碰撞
    API 盘点（collection self-collision indicator 实测无效、conveyor 粗
    代理 `shape`）；
  - `stage2_ik_candidates_20261021_105624.json` — 多解 IK 安全分支
    （pick/station/agv）；
  - `phase2_mcp_station_none_20261021_191502.json` — MCP 按工件
    `source_phase` 路由到阶段二 runner 的 station 运行。

- 阶段 2.01 动作与轨迹优化（2026-10-21，工件
  `stage2/L1_S1/v012_20261021_210539`）：
  - `phase2_01_baseline_station_20261021_202313.json` — 优化前 station
    （first-feasible，transfer TV 15.644，pre_place 10.595）；
  - `phase2_01_run_station_none_20261021_210605.json` — 优化后 station：
    DONE，物料误差 0.00013 m，总 TV 11.966，transfer TV 7.601；
  - `phase2_01_run_agv_none_20261021_210713.json` — agv：DONE，跟随误差
    0.0100 m，轮速行驶 0.505 m；
  - `phase2_01_run_both_none_20261021_210846.json` — station+agv DONE，
    无串任务；
  - `phase2_01_run_station_runtime_obstacle_20261021_211101.json` — 运行期
    障碍锁存，`TRANSFERRING` 失败段实测证据保留，停带确认 4e-5 m/s；
  - `phase2_01_motion_opt_20261021_211245.json` — 腕部分支/锁轴/拆分 A/B：
    production 与 no_split 一致，split 增加 station +29.2%、AGV +11.6%；
  - `phase2_01_motion_opt_nobranch_20261021_211837.json` — 关闭腕部守卫的
    对照；真实运行仍要求守卫，跨支候选一律拒绝。

- 阶段 2.02 AGV 转运路径优化（2026-10-24/25，工件
  `stage2/L1_S1/v013_20261024_232558`，契约
  `stage2_contract_20261024_v3`）：
  - `phase2_02_run_station_none_20261024_235047.json` — 默认 baseline/greedy
    回归：DONE，总 TV 11.9698，含逐段执行 profile；
  - `phase2_02_run_agv_none_20261024_235155.json` — 默认 AGV：DONE，
    transfer 计划 TV 14.3453 / 执行 TV 14.3368，q[3] 净变化 +5.0195，
    复现 §8.1 现象；
  - `phase2_02_run_both_none_20261025_000623.json` — station+agv DONE，
    无串任务；
  - `phase2_02_run_station_runtime_obstacle_20261025_000901.json` — 运行期
    障碍锁存 `link3_visible <-> Fault_Runtime_Obstacle`，停带确认 4e-5 m/s；
  - `phase2_02_agv_transfer_probe_20261025_000100.json` — 影子规划 A/B：
    baseline 0.20/0.10/0.00 m 与 high_align、detour；greedy 与 beam 一致，
    0.10/0.00 m transfer TV 降低约 47%、q[3] 降低约 79.5%，但 q[0] 升高，
    默认仍保留 0.20 m；
  - `phase2_02_agv_transfer_repeat_20261025_000953.json`、
    `phase2_02_agv_transfer_repeat_20261025_001111.json` — 同一起始 q/pick
    pose 的重复规划；与主探针三次结果逐项一致；
  - `phase2_02_agv_transfer_detour_20261025_001651.json` — detour 路径
    探针：0.20/0.10/0.00 m 下加入 +0.06 m 水平绕行点均比对应 high_align
    增加 TV（+2.2%/+3.7%/+5.9%），本次无直线路径被拒绝，故不启用。
