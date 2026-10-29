# 测量材料归档

本目录保存测量/分析程序和未在证据目录归档的原始 JSON、JSONL 结果。它们用于复现布局、运动学、规划、物理与运行行为的观测，不等同于正式阶段验收报告。

## 按用途查找

| 用途 | 文件 |
|---|---|
| 阶段零参数和几何基线 | `measure_phase0.py`、`phase0_measurements.json`、`ik_probe.py` |
| 步骤 1.1 布局与取料实验 | `analyze_step1_1.py`、`check_step1_2.py`、`agv_drive_test.py`、`agv_drive_test.json` |
| 阶段二场景/规划探针 | `probe_stage2_inventory.py`、`probe_stage2_ik_candidates.py`、`probe_stage2_planning.py`、`probe_stage2_path_rejection.py` |
| 阶段 2.01/2.02 动作与 AGV 路径实验 | `probe_stage2_motion_opt.py`、`probe_stage2_transfer_branches.py`、`probe_stage2_transfer_search.py`、`probe_stage2_agv_transfer.py` |
| 阶段三布局测量 | `probe_stage3_layout.py` |
| 阶段四布局与五台臂 IK 测量 | `probe_stage4_layout.py`（结果归档为 `../implementation/evidence/stage4_layout_20261029_v008.json`） |
| 阶段四运行报告提取 | `analyze_stage4_run.py` |
| 仓库级完整测量索引 | [wiki/06-工业案例](../wiki/06-工业案例/) 对应阶段方案与报告；当前证据副本索引见 [implementation/evidence/README.md](../implementation/evidence/README.md) |

## 原始结果与正式证据

- 本目录保留独有的探针原始输出或特定测量结果，保留生成时的时间戳，不覆盖历史结果。
- 正式报告引用的 JSON/JSONL 归档在 `docs/implementation/evidence/`。若测量目录中的文件与证据副本内容完全相同，只保留证据目录副本，并将引用指向该文件，避免重复存储。
- 临时运行报告写入 `docs/implementation/runs/`，该目录被 Git 忽略，不属于提交归档。
- 新增测量脚本时，在文件头部说明用途、前置场景/工件和输出路径；新增数据时在对应报告或证据索引中说明结论用途。
