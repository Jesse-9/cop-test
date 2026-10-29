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

- 06-15 水平带载转运与放置下探（2026-10-28，工件
  `stage2/L1_S1/v014_20261028_175742`、`stage3/L2_MIRROR/v006_20261028_180722`）：
  - `scene_validation_stage2_L1_S1_v014_20261028_175742_20261028_175802.json`
    — stage2 v014 只读校验通过（契约 `stage2_contract_20261028_v4`）；
  - `phase2_run_station_none_20261028_180806.json` — station：直线第 48 层
    `IK_FAILED`，`horizontal_detour_+0.30` 成功；水平段 z 漂移 2.56e-4 m，
    5 个运动阶段 DONE，`all_ok`；
  - `phase2_run_agv_none_20261028_181035.json` — AGV：直线转运，z 漂移
    2.27e-4 m，5 个运动阶段 DONE，AGV follow ok；
  - `phase2_run_both_none_20261028_181920.json` — station（绕行）+ agv
    （直线）两任务 DONE，无串任务；
  - `phase2_run_station_runtime_obstacle_20261028_182301.json` — 运行期
    障碍在 TRANSFERRING 检出 `Fault_Runtime_Obstacle` 碰撞并锁存，停止确认；
  - `scene_validation_stage3_L2_MIRROR_v006_20261028_180722_20261028_181245.json`
    — stage3 v006 只读校验通过（父 stage2 v014）；
  - `phase3_run_closed_loop_20261028_181245.json` — A/B 各 5 个运动阶段
    DONE，水平段 z 漂移 1.98e-4 / 1.67e-4 m，两站任务全部 `TASK_DONE`；
  - `phase3_run_unstable_unload_20261028_181559.json` — 注入
    `phase=RELEASING`；`LOAD_DRIFT(0.0400>0.02)` 在释放前锁存，
    `release_attempted=false`，载荷仍 `HELD_BY_SUCTION`。

- 阶段三双工位柔性搬运（2026-10-25，工件
  `stage3/L2_MIRROR/v004_20261025_131837`，父
  `stage2/L1_S1/v013_20261024_232558`，契约
  `stage3_contract_20261025_v1`）：
  - `phase3_run_closed_loop_20261025_140903.json` — A→B 闭环：3 任务
    DONE，停靠误差 0.019733 m，航向 −0.000203 rad，载荷滑移 0.0；
  - `phase3_run_contention_20261025_132245.json` — 3 个重复请求被拒绝，
    dock_B 等待 1 次，资源最终全部释放；
  - `phase3_run_blocked_20261025_141704.json` — t=44.90 进入 BLOCKED，
    t=54.95 清障续行，停靠误差 0.019773 m；
  - `phase3_run_unstable_unload_20261025_134050.json` — 释放前
    `LOAD_DRIFT(0.0400>0.02)` 锁存；unload FAILED，载荷仍
    `HELD_BY_SUCTION`，`release_attempted=false`，停止已确认；
  - `phase3_run_route_baseline_20261025_133223.json` — A→B 带载
    0.019742 m、B→A 空载 0.019522 m，滑移 0.0；
  - `phase3_mcp_closed_loop_20261025_133838.json` — MCP 自动 backend=stage3，
    三任务 DONE，生命周期/运行锁复用；
  - `scene_validation_stage3_L2_MIRROR_v004_20261025_131837_20261025_134050.json`
    — v004 工件 229 项只读校验通过；
  - `stage3_layout_20261025.json` — `L2_MIRROR` 50 项静态检查通过、
    `L3_SAME_SIDE` 被拒、当前场景两工位/AGV dock 只读测量。

- 阶段四五岛生产（2026-10-28/29，工件
  `stage4/L4_COLUMN/v008_20261028_225634`，父
  `stage3/L2_MIRROR/v006_20261028_180722`，契约
  `stage4_contract_20261028_v1`）：
  - `phase4_analysis_full_flow_20261028_234140.json` — 13/13 任务
    DONE，1 个 FINAL（父件 1/3），1 个未配对 SEMI_CE；四条路线停靠
    误差 0.0196 m，滑移 0.0；
  - `phase4_analysis_no_pair_20261029_000629.json` — 7/7 任务 DONE，
    1 个 FINAL（父件 1/2），无未配对半成品；
  - `phase4_analysis_agv_priority_20261029_001906.json` — 13/13 任务
    DONE，两次派车均满足优先级，`no_preemption = true`；
  - `phase4_analysis_agv_blocked_20261029_010133.json` — B→A 与
    D→E 各一次 BLOCKED/清障续行，路线 29.6 s / 66.3 s，1 个 FINAL；
  - `phase4_analysis_conveyor_fault_20261029_012444.json` — 停
    `line_ce` 后 `e_conveyor` BLOCKED、整线冻结、无 FINAL、资源释放；
  - `scene_validation_stage4_L4_COLUMN_v008_20261028_225634_20261029_012444.json`
    — v008 启动点 452 项只读校验通过；
  - `stage4_layout_20261029_v008.json` — 145 项静态检查、185 项场景
    契约检查通过，五臂 13 个 IK 目标全部成功；
  - `phase3_run_closed_loop_20261029_013344.json` — 阶段三 v006
    回归，三任务 DONE，停靠误差 0.019924 m，滑移 0.0。

  阶段四原始运行报告（6–18 MB/份）保留在
  `docs/implementation/runs/phase4_run_*.json`，不纳入版本控制；
  本目录的分析摘要由 `docs/measurement/analyze_stage4_run.py` 生成，
  含验收、任务、路线、物料与指标字段。
