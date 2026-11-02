# 仿真运行录制报告：阶段一至四多条件视频

日期：2026-10-30
环境：CoppeliaSim 4.10.0.0，ZeroMQ Remote API（localhost:23000）
工具：[record_stage.py](../../tools/record_stage.py)、[record_stage_matrix.py](../../tools/record_stage_matrix.py)
运行产物：`docs/implementation/runs/recordings/`（被 `.gitignore` 排除）

> 本报告记录 2026-10-30 对阶段一至阶段四在不同 target/scenario/fault 条件下的
> 仿真运行录制。视频为本地运行产物，不随仓库分发；本报告给出文件名、时长、
> 条件含义和未完成项，配合同名 `.json` 元数据即可复核。

## 1. 结论

- 共计划 24 个条件：阶段一 9 个（3 target + 6 故障）、阶段二 4 个（3 target +
  `runtime_obstacle`）、阶段三 5 个场景、阶段四 6 个场景。
- 已完成 23 个条件的完整录像；`stage4/staggered_batch` 只有 313 s 部分录像，
  未覆盖完整 2/1/2 批次（原因见 §5）。
- 24 个 MP4 均为 H.264、1280×720、20 fps，全部通过解码校验；视频合计约 22 MB。
- 录制过程不修改场景工件、`current` 指针或受保护文件；录制相机在启动点校验
  通过后才创建，并在 runner 退出后移除。

## 2. 录制方法

1. `record_stage.py` 先确认仿真处于停止态，再以子进程启动对应阶段 CLI runner。
2. runner 完成工件加载与启动点校验、进入 stepping 后，工具才创建自动处理的
   vision sensor（别名 `RecordingCamera`），避免影响启动点场景校验。
3. 按固定仿真时间间隔抽帧（正常推进 20 Hz 轮询，长时间计算时退避），原始缓冲区
   经 `vflip` 后由 `imageio-ffmpeg` 编码为 H.264；播放速度 = 抽帧间隔的倒数 / 20 fps。
4. runner 停止仿真后结束录制，写入同名 `.json`（机位、抽帧、帧数、时长、退出码）
   和 `.log`（runner 日志）。

机位（世界坐标，m / 度）：

| 阶段/条件 | 相机位置 → 目标 | 视场角 | 抽帧间隔 |
|---|---|---|---|
| 1 station / 非 AGV 故障 | (2.00, -1.75, 1.60) → (-0.25, 0.65, 0.45) | 45° | 0.05 s |
| 1 agv / both / agv_blocked | (2.55, -2.25, 1.85) → (-0.10, 0.60, 0.42) | 48° | 0.05 s |
| 2 station / runtime_obstacle | (2.05, 2.75, 1.95) → (-0.20, 0.45, 0.38) | 50° | 0.10 s |
| 2 agv / both | (2.55, -2.25, 1.85) → (-0.10, 0.60, 0.42) | 48° | 0.10 s |
| 3 全部场景 | (2.30, -3.10, 2.05) → (-0.30, 0.05, 0.35) | 46° | 0.10 s |
| 4 全部场景 | (2.80, -4.80, 3.30) → (1.05, -0.05, 0.35) | 52° | 0.15 s |

## 3. 已录制清单

| 阶段 | 条件 | 内容 | 文件（`runs/recordings/`） | 视频时长 | 仿真时长 | 速度 | 退出码 |
|---|---|---|---|---|---|---|---|
| 1 | `station` | 输送带取料 → 放置到置物台 | `stage1_station_20261030_101400.mp4` | 13.7 s | 13.7 s | 1.0× | 0 |
| 1 | `agv` | 取料 → 放到 AGV 托盘 → AGV 前进 0.5 m | `stage1_agv_20261030_101643.mp4` | 14.7 s | 14.7 s | 1.0× | 0 |
| 1 | `both` | station + agv 两次搬运 | `stage1_both_20261030_101747.mp4` | 29.6 s | 29.5 s | 1.0× | 0 |
| 1 | `ik_unreachable` | 放置点移出工作空间，IK 不可达 | `stage1_station_fault_ik_unreachable_20261030_101905.mp4` | 7.2 s | 7.1 s | 1.0× | 1 |
| 1 | `no_target` | 传送带不送料，物料不到达 | `stage1_station_fault_no_target_20261030_101933.mp4` | 16.0 s | 15.9 s | 1.0× | 1 |
| 1 | `uid_mismatch` | 取料位放入非目标物料 | `stage1_station_fault_uid_mismatch_20261030_102016.mp4` | 16.0 s | 15.9 s | 1.0× | 1 |
| 1 | `sensor_disabled` | 移除取料传感器 | `stage1_station_fault_sensor_disabled_20261030_102101.mp4` | 3.1 s | 3.1 s | 1.0× | 1 |
| 1 | `agv_blocked` | AGV 路径放置障碍（本次 station 目标未经过 AGV） | `stage1_station_fault_agv_blocked_20261030_102119.mp4` | 13.7 s | 13.7 s | 1.0× | 0 |
| 1 | `release_unstable` | 释放不稳定/载荷漂移 | `stage1_station_fault_release_unstable_20261030_102201.mp4` | 13.6 s | 13.6 s | 1.0× | 1 |
| 2 | `station` | 取料 → 放置到置物台（规划/碰撞约束） | `stage2_station_20261030_092238.mp4` | 22.0 s | 43.9 s | 2.0× | 0 |
| 2 | `agv` | 取料 → 放到 AGV 托盘（规划/碰撞约束） | `stage2_agv_20261030_102242.mp4` | 21.5 s | 42.9 s | 2.0× | 0 |
| 2 | `both` | station + agv 连续任务 | `stage2_both_20261030_102533.mp4` | 45.2 s | 90.4 s | 2.0× | 0 |
| 2 | `runtime_obstacle` | 搬运中注入动态障碍 | `stage2_station_fault_runtime_obstacle_20261030_103114.mp4` | 6.0 s | 12.0 s | 2.0× | 1 |
| 3 | `closed_loop` | A 取料 → AGV 转运 → B 卸料 | `stage3_closed_loop_20261030_092538.mp4` | 40.8 s | 81.4 s | 2.0× | 0 |
| 3 | `contention` | 共享取料区/资源锁竞争 | `stage3_contention_20261030_103246.mp4` | 40.5 s | 81.0 s | 2.0× | 0 |
| 3 | `blocked` | AGV 通道阻塞 | `stage3_blocked_20261030_103801.mp4` | 50.9 s | 101.7 s | 2.0× | 0 |
| 3 | `unstable_unload` | B 卸载不稳定，联锁锁存 | `stage3_unstable_unload_20261030_104358.mp4` | 39.0 s | 78.0 s | 2.0× | 0 |
| 3 | `route_baseline` | AGV 直线转运基线 | `stage3_route_baseline_20261030_104901.mp4` | 46.7 s | 93.3 s | 2.0× | 0 |
| 4 | `full_flow` | S1/S2 → A/B/C/D/E → 成品输出完整流程 | `stage4_full_flow_20261030_093111.mp4` | 112.5 s | 337.5 s | 3.0× | 0 |
| 4 | `no_pair` | 无配对批次条件 | `stage4_no_pair_20261030_105446.mp4` | 54.9 s | 164.6 s | 3.0× | 0 |
| 4 | `agv_priority` | AGV 优先级仲裁 | `stage4_agv_priority_20261030_111042.mp4` | 112.5 s | 337.2 s | 3.0× | 0 |
| 4 | `agv_blocked` | AGV 阻塞条件 | `stage4_agv_blocked_20261030_113842.mp4` | 125.2 s | 375.4 s | 3.0× | 0 |
| 4 | `conveyor_fault` | C→E 传送带故障停带 | `stage4_conveyor_fault_20261030_120842.mp4` | 31.7 s | 95.0 s | 3.0× | 0 |
| 4 | `staggered_batch` | 交错批次 2/1/2；本次为部分录制 | `stage4_staggered_batch_20261030_140633.mp4` | 104.5 s | 313.4 s | 3.0× | 部分 |

“退出码”是阶段 runner 的进程退出码：0 表示该次运行自检通过，1 表示故障/异常
条件按设计以失败结束。阶段一/二的故障注入与阶段二 `runtime_obstacle` 属后者。
阶段四 `staggered_batch` 的“部分”表示 runner 在 D 臂 `IK_FAILED` 后以失败码结束，
视频只覆盖到 313.4 s。

## 4. 分阶段说明

### 4.1 阶段一（单工位闭环）

- `station`：从传送带取料并放置到置物台；完整闭环约 13.7 s。
- `agv`：取料后放到 AGV 托盘，AGV 前进 0.5 m；约 14.7 s。
- `both`：station 与 agv 两个任务连续执行；约 29.6 s。
- 六个故障分别覆盖 IK 不可达、无来料、UID 不匹配、取料传感器移除、AGV 路径
  障碍和不稳定释放；除 `agv_blocked` 外均以 runner 退出码 1 结束。

### 4.2 阶段二（物理约束与运动规划）

- `station`/`agv`/`both` 与阶段一目标相同，但经过规划、碰撞检查和安全联锁；
  仿真时长 42.9–90.4 s，视频 2× 播放。
- `runtime_obstacle`：搬运过程中在机械臂路径注入动态障碍，12 s 时按预期失败。

### 4.3 阶段三（双工位柔性搬运）

- `closed_loop`：A 取料 → AGV 直线转运 → B 卸料。
- `contention`：共享取料区/资源锁竞争条件下的调度。
- `blocked`：AGV 通道出现障碍后的受控阻塞与恢复。
- `unstable_unload`：B 卸载载荷漂移触发联锁锁存，任务拒绝完成。
- `route_baseline`：不带竞争/故障的 AGV 转运基线。

### 4.4 阶段四（多岛车间 L5_UFLOW）

- `full_flow`：S1/S2 → A/B/C/D/E → 成品输出的完整物流链。
- `no_pair`：无配对批次条件。
- `agv_priority`：AGV 任务优先级仲裁。
- `agv_blocked`：AGV 阻塞条件。
- `conveyor_fault`：C→E 传送带故障停带。
- `staggered_batch`：2/1/2 交错批次；仅录到 313.4 s 部分过程（见 §5）。

## 5. `stage4/staggered_batch` 未完成说明

该场景在本次录制中连续遇到偶发失败，未得到完整成功运行：

| 尝试 | 结果 |
|---|---|
| 12:19 | 规划最终复核墙钟 805 s，超出 60 s 预算（`PLAN_BUDGET_EXCEEDED`），在 10.8 s 中止 |
| 13:40 | A 臂 `RELEASE_CORRECTION_EXCEEDED`（pos=0.0111 m, ang=0.0248 rad），230 s 失败 |
| 14:06 | 314 s 时 D 臂 `IK_FAILED`；录制在失败后停止，保留 313.4 s / 104.5 s 视频 |
| 15:17 | 按用户要求停止录制，未产生有效帧 |

历史记录中该场景在无录制条件下成功过：2026-10-29 17:09 完成 549 s、`all_ok=true`；
2026-10-29 16:59 也曾在 92 s 因 B 臂 `RELEASE_CORRECTION_EXCEEDED` 失败。
因此判断为场景本身在规划/释放精度上的偶发不稳定，而不是录制相机改变了场景物理。
如需补齐，可在空闲时重跑：

```
.venv/Scripts/python.exe tools/record_stage.py --stage 4 --scenario staggered_batch --freeze-timeout 3600
```

## 6. 未录制条件

| 条件 | 说明 |
|---|---|
| 阶段四 `staggered_batch` 完整成功运行 | 当前只有 313 s 部分视频 |
| 阶段一 `station --repeat 6 --time 30` | 6 次连续搬运的浸泡运行 |
| 阶段一 `agv_blocked` + `--target agv`/`both` | 本次 agv_blocked 用默认 station，未展示 AGV 被阻塞 |
| 阶段二其余六个故障 | `ik_unreachable`/`no_target`/`uid_mismatch`/`sensor_disabled`/`agv_blocked`/`release_unstable` |
| 阶段一/二故障 + `agv`/`both` 的其余组合 | 故障视频均用默认 station |
| 阶段三/四显式 `--fault` 覆盖 | 场景已含默认故障，未单独录制 |
| 旧布局 `stage4/L4_COLUMN`、`src/reference/` 五岛、`stage1_1` | 非当前主线 |

## 7. 备注与限制

- 阶段一 `agv_blocked` 采用默认 `--target station`：障碍位于 AGV 路径，但工位任务
  不经过 AGV，视频展示的是“障碍存在、工位任务仍完成”，并非 AGV 被阻塞。
- 视频与 `INDEX.md` 位于 `docs/implementation/runs/recordings/`，该目录被 Git 忽略；
  需要长期随版本追溯时应将文件复制到 `docs/implementation/evidence/` 并更新
  `evidence/README.md`。
- 录制相机是运行期临时对象，不进入工件 manifest；24 次运行均通过启动点只读校验，
  运行结束后仿真恢复为 `SCENE_VALIDATED`、`sim_state=0`。
- 录制抽帧与编码会占用主机资源，长场景（阶段四）的墙钟时间明显长于仿真时间；
  视频时长与仿真时长的比例由 `sample_dt` 固定，与墙钟无关。

## 8. 复现命令

```
.venv/Scripts/python.exe tools/record_stage.py --stage 1
.venv/Scripts/python.exe tools/record_stage.py --stage 2 --target agv
.venv/Scripts/python.exe tools/record_stage.py --stage 1 --fault ik_unreachable
.venv/Scripts/python.exe tools/record_stage_matrix.py            # 全部条件，跳过已有
.venv/Scripts/python.exe tools/record_stage_matrix.py --force    # 全部重录
```

逐条视频说明与机位参数另见本地索引 `runs/recordings/INDEX.md`；
使用方法见 [项目使用指南](../wiki/98-ProjectUsage/USAGE.md) 的“录制仿真运行过程”一节。
