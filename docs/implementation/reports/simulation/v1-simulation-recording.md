---
title: v1 运行录像（仿真视频）设计与实现报告
type: report
status: review
created: 2026-11-24
updated: 2026-11-24
scope: v1 运行录像（CLI/API/前端）与视频接口
---

# v1 运行录像（仿真视频）设计与实现报告

日期：2026-11-24
范围：`flexible-factory` v1（`src/flexible_factory/`）与前端订单工作台
状态：已实现并通过离线测试与一次 20 s 实机短片验证；**完整 `full_flow` 录像尚未执行**

## 1. 结论

- 仿真录像可以接入前端：每次订单运行在启动对话框单独选择是否录制，运行结束后
  在运行页播放/下载 MP4。
- 同一录制器同时接入 `flexible-factory run` 与 `flexible-factory order start`，
  命令行可用 `--record on` 显式开启；默认关闭，不改变既有基线速度与结果。
- 录制是运行旁路：编码/写盘/相机失败只把 `recording.status` 标记为
  `failed`/`partial`，不改变运行结果、退出码或运行记录。
- v1 用户运行模式只有 `full_flow`，前端也只触发该场景；CLI 仍保留 5 个内部
  回归场景（`no_pair`、`agv_priority`、`agv_blocked`、`conveyor_fault`、
  `staggered_batch`），同一录制器可以覆盖，但前端不触发。
  `planner_test`/`compiler_only` 不启动仿真，不产生视频；旧阶段一至三场景
  已随代码删除，无法录制。

## 2. 需求讨论记录

| 问题 | 结论 |
|---|---|
| 能否接入前端界面 | 可以。录制点放在持有 stepping 的 worker 线程内（`order_runner.run_order_once`），不另开 ZMQ 连接；新增 Range 视频接口供 `<video>` 播放。 |
| 是否每次运行可选 | 是。每次启动前单独选择；只对本次运行生效，一次运行一个文件。 |
| 命令行是否也能加参数 | 是。`run`、`order start`、`api serve` 共用同一组 `--record*` 参数。 |
| 是否所有场景都支持 | 只要运行真实仿真就能录：前端 `full_flow`、CLI 6 个场景。无仿真的 `planner_test`/`compiler_only` 不能录；已删除的旧阶段场景不可录。 |
| 录制失败怎么办 | 不阻断运行：标记 `failed`/`partial` 并写日志；显式 `--record on` 时若 ffmpeg 不可用，在连接仿真前直接报错退出。 |
| 录像覆盖多长 | 从 `start_stepping()` 后到运行结束/失败/手动停止的整段动作；场景加载和启动点校验不录。运行中止时保留已录部分并标记 `partial`。 |

## 3. 设计

### 3.1 录制点与生命周期

- 相机在启动点校验（`manager.prepare_start()`）通过、`start_stepping()` 之后创建，
  避免影响场景校验；对象别名 `RecordingCamera`，不写入场景工件。
- 每步 `coordinator.step()` 之后调用 `capture_if_due()`；按固定仿真时间间隔抽帧，
  落后时用当前帧补齐采样槽，保证视频时长与仿真时间成固定比例。
- 运行结束或异常时移除相机、关闭编码器、写元数据 JSON；`close()` 可重复调用，
  且不向调用方抛异常。
- 录制机位由场景地面范围推导（东南高位、52°），L5_UFLOW 默认地面
  （8 m × 6 m，原点居中）得到 `(2.8, -4.8, 3.3) → (1.04, -0.06, 0.35)`，
  与历史阶段四录制机位一致。

### 3.2 失败语义

| 情况 | 行为 |
|---|---|
| 未开启 | 不创建相机/编码器，零开销 |
| 显式 `--record on` 但 ffmpeg 不可用 | CLI/API 在创建 run、连接仿真前返回配置错误 |
| 运行中编码/相机失败 | `status=failed`，运行继续；日志告警 |
| 运行异常中止 | 已录部分保留，`status=partial`，元数据带 `error` |
| 未采到任何帧 | `status=failed`，删除空 MP4 |

### 3.3 输出与版本

- 目录：`var/recordings/`（`spec.RECORDING_DIR`，被 `.gitignore` 排除，不写入场景
  工件目录）；文件名 `<run_id>.mp4`，同名 `.json` 记录状态、帧数、仿真/视频时长、
  机位、抽帧间隔和错误。
- 版本：`spec.RECORDING_VERSION = "v1_recording_20261124"`，只随抽帧/编码/元数据
  语义变化递增；不参与 `config_digest()`/`layout_digest()`，不写入订单版本或 DAG。
- 运行报告与运行记录的结果字段新增 `recording`；API 运行详情顶层也返回
  `recording`，前端据此显示。

## 4. 实现

| 层 | 改动 |
|---|---|
| 录制器 | 新增 `application/recording.py`：`RecordingOptions`、`SimulationRecorder`、`FFmpegEncoder`、`camera_view`、`make_recorder`、`ensure_available` |
| CLI | `run`/`order start`/`api serve` 共用 `--record {on,off}`、`--record-dir`、`--record-size`、`--record-sample-dt`；`api serve` 用 `--record-default` |
| 订单服务 | `OrderRunCommand.record`、`OrderService.start()/run_sync(record=...)`、启动前 ffmpeg 检查、`video_path()` |
| 订单 runner | `OrderRunnerConfig` 增加录像目录/分辨率/抽帧；worker 内创建/抽帧/收尾，进度快照含 `recording` |
| CLI runner | `run_loaded_scene_v1(recording=...)`，`run_scenario_v1` 每步抽帧；summary 增加 `recording` |
| HTTP | 启动 body 接受 `record` 布尔值；新增 `GET /api/runs/{run_id}/video`，支持 Range/206 |
| 前端 | 启动对话框增加“录制本次运行”；运行页增加录像面板（播放/下载/失败提示） |
| 依赖 | `imageio-ffmpeg` 从 dev 依赖移入运行时依赖（自带 ffmpeg 二进制） |

## 5. 验证

```text
后端：813 passed, 8 skipped
前端：16 passed；typecheck、vite build 通过
新增：tests/test_v1_recording.py（18 项）+ 订单服务 record/recording 投影（2 项）
```

新增测试覆盖：分辨率解析、选项校验、禁用时零对象、按仿真时间抽帧与补帧、
运行中止标记 `partial`、编码失败不外抛、真实 ffmpeg 编码可生成文件、命令
`record` 字段、视频接口 206 Range 与 409 无录像。

**实机短片验证（2026-11-24）**：在当前 v1 工件 `v005` 上执行两次
`run --no-load --record on`（20 s / 640×360 / 0.2 s 与 10 s / 1280×720 /
0.1 s），均生成 99 帧 / 4.95 s 视频、`recording.status=ok`，机位正确覆盖
L5_UFLOW 五岛与 AGV；运行按时间上限结束（`all_ok=false`，属预期）。两次运行的
元数据摘要见 `evidence/simulation/v1_recording_smoke_20261124.json`。
**完整 `full_flow` 时长录像、掉帧率和每步渲染开销仍未测量**；历史（v1 前）
录制工具曾实测自动 vision sensor 使 20 步耗时约 1.07 s → 1.73 s，v1 场景
更大，不能直接引用旧数据作为 v1 结论。

## 6. 限制与非目标

- 只有文件产物，没有实时推流；运行结束（或中止）后才能播放。
- 单 worker 同一时刻只跑一个运行，因此一次只有一个录像，没有并发冲突。
- 不叠加事件字幕/传感器覆盖层，不录声音。
- 相机位置按地面范围推导，不随任务自动跟拍；极小场景可能构图偏差，可用
  `--record-size`/`--record-sample-dt` 调整画质与时长，但不提供自由机位参数。
- `planner_test`/`compiler_only` 与已删除的旧阶段场景不在支持范围。