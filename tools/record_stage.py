#!/usr/bin/env python3
"""record_stage.py — 录制阶段一到阶段四的仿真运行过程 (MP4).

用法:
    .venv/Scripts/python.exe tools/record_stage.py --stage 1
    .venv/Scripts/python.exe tools/record_stage.py --stage 2
    .venv/Scripts/python.exe tools/record_stage.py --stage 3
    .venv/Scripts/python.exe tools/record_stage.py --stage 4
    # 可选: --out 指定文件, --sample-dt 覆盖默认抽帧间隔, --fov/--cam-pos 覆盖机位

工作方式:
  1. 先确保 CoppeliaSim 处于停止态, 再以子进程启动对应阶段的 CLI runner;
  2. 等 runner 完成工件加载/启动点校验并开始 stepping 后, 才创建一个
     自动处理的 vision sensor 作为录制相机 (避免改动启动点校验时的场景);
  3. 按 --sample-dt 的仿真时间间隔抓帧, 通过 ffmpeg (imageio-ffmpeg 自带)
     编码成 H.264 MP4; 若某次抓帧落后, 用当前帧补齐时间槽, 保证视频
     时长与仿真时间成固定比例;
  4. runner 停止仿真后结束录制, 写同名 .json 记录机位/抽帧/帧数,
     并在终端打印汇总.

机位按各阶段布局给出 (世界坐标, m/rad), 只影响录制, 不写入场景工件.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUT_DIR = ROOT / "docs" / "implementation" / "runs" / "recordings"

# ── 各阶段机位与默认抽帧间隔 ─────────────────────────────────────
# sample_dt = 每帧对应的仿真时间 (s); 视频帧率固定 20 fps 时,
# 播放速度 = 1/sample_dt / 20 (倍).
# 阶段一/二按运行条件切换: 纯工位/故障用近景, 含 AGV 行驶用宽景.
STAGE1_CLOSE_PRESET = {
    "cam_pos": (2.00, -1.75, 1.60),
    "cam_target": (-0.25, 0.65, 0.45),
    "fov": 45.0,
    "sample_dt": 0.05,          # 1x
}
STAGE2_CLOSE_PRESET = {
    "cam_pos": (2.05, 2.75, 1.95),
    "cam_target": (-0.20, 0.45, 0.38),
    "fov": 50.0,
    "sample_dt": 0.10,          # 2x
}
WIDE_AGV_PRESET = {
    "cam_pos": (2.55, -2.25, 1.85),
    "cam_target": (-0.10, 0.60, 0.42),
    "fov": 48.0,
}

STAGE_PRESETS = {
    1: dict(STAGE1_CLOSE_PRESET),
    2: dict(STAGE2_CLOSE_PRESET),
    3: {
        "label": "closed_loop",
        "cam_pos": (2.30, -3.10, 2.05),
        "cam_target": (-0.30, 0.05, 0.35),
        "fov": 46.0,
        "sample_dt": 0.10,          # 2x
    },
    4: {
        "label": "full_flow",
        "cam_pos": (2.80, -4.80, 3.30),
        "cam_target": (1.05, -0.05, 0.35),
        "fov": 52.0,
        "sample_dt": 0.15,          # 3x
    },
}

DEFAULT_TIME = {
    1: {"station": 45.0, "agv": 45.0, "both": 90.0},
    2: {"station": 150.0, "agv": 150.0, "both": 180.0},
    3: {"closed_loop": 300.0, "contention": 300.0, "blocked": 300.0,
        "unstable_unload": 300.0, "route_baseline": 300.0},
    4: {"full_flow": 700.0, "no_pair": 500.0, "agv_priority": 700.0,
        "agv_blocked": 700.0, "conveyor_fault": 400.0,
        "staggered_batch": 900.0},
}


def _default_time(stage: int, target: str, scenario: str) -> float:
    table = DEFAULT_TIME[stage]
    return float(table.get(target if stage <= 2 else scenario, 300.0))


# ═══════════════════════════════════════════════════════════════
# 几何 / 机位
# ═══════════════════════════════════════════════════════════════

def _normalize(v):
    n = math.sqrt(sum(c * c for c in v))
    return tuple(c / n for c in v)


def _cross(a, b):
    return (a[1] * b[2] - a[2] * b[1],
            a[2] * b[0] - a[0] * b[2],
            a[0] * b[1] - a[1] * b[0])


def look_at_matrix(pos, target) -> list[float]:
    """世界 Z 向上、+Z 朝目标的相机位姿, 输出 sim.setObjectMatrix 的 3x4.

    vision sensor 的 getVisionSensorImg 原始缓冲区是 OpenGL 自下而上的行序
    (sim.saveImage 会自动翻转, 直接送 ffmpeg 时必须加 vflip).
    相机局部轴: X=屏幕右 (up × forward), Y=上, Z=视线方向.
    """
    forward = _normalize(tuple(target[i] - pos[i] for i in range(3)))
    right = _normalize(_cross((0.0, 0.0, 1.0), forward))
    up = _cross(forward, right)
    return [right[0], up[0], forward[0], pos[0],
            right[1], up[1], forward[1], pos[1],
            right[2], up[2], forward[2], pos[2]]


# ═══════════════════════════════════════════════════════════════
# 仿真连接
# ═══════════════════════════════════════════════════════════════

def connect(host: str, port: int):
    from coppeliasim_zmqremoteapi_client import RemoteAPIClient
    client = RemoteAPIClient(host=host, port=port)
    return client.require("sim")


def wait_stopped(sim, timeout: float = 30.0) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if int(sim.getSimulationState()) == sim.simulation_stopped:
            return True
        time.sleep(0.05)
    return False


def _runner_command(stage: int, ns) -> list[str]:
    """按阶段拼 CLI; 与 docs/wiki/98-ProjectUsage/USAGE.md 中的命令一致."""
    if stage == 1:
        cmd = [str(ROOT / "src" / "run_stage1.py"),
               "--target", ns.target, "--time", str(ns.time)]
    elif stage == 2:
        cmd = [str(ROOT / "src" / "run_stage2.py"),
               "--target", ns.target, "--time", str(ns.time)]
    elif stage == 3:
        cmd = [str(ROOT / "src" / "run_stage3.py"),
               "--scenario", ns.scenario, "--time", str(ns.time)]
    else:
        cmd = [str(ROOT / "src" / "run_stage4.py"),
               "--scenario", ns.scenario, "--time", str(ns.time)]
    if ns.fault and ns.fault != "none":
        cmd += ["--fault", ns.fault]
    return cmd


def _preset_for(stage: int, target: str, scenario: str, fault: str) -> dict:
    """按运行条件选机位: 涉及 AGV 行驶的用更宽的东南视角."""
    preset = dict(STAGE_PRESETS[stage])
    if stage in (1, 2) and (target in ("agv", "both")
                            or fault == "agv_blocked"):
        preset.update(WIDE_AGV_PRESET)
    return preset


# ═══════════════════════════════════════════════════════════════
# 录制
# ═══════════════════════════════════════════════════════════════

def record(sim, ns, preset: dict, video_path: Path, log_path: Path,
           meta_path: Path) -> dict:
    try:
        import imageio_ffmpeg
    except ImportError as exc:                     # pragma: no cover
        raise SystemExit(
            "缺少 imageio-ffmpeg (自带 ffmpeg)。安装: "
            "uv sync --group dev 或 uv pip install imageio-ffmpeg") from exc

    width, height = ns.width, ns.height
    fps = float(ns.fps)
    sample_dt = float(ns.sample_dt or preset["sample_dt"])

    ffmpeg = imageio_ffmpeg.get_ffmpeg_exe()
    video_path.parent.mkdir(parents=True, exist_ok=True)
    cmd = [ffmpeg, "-y", "-loglevel", "error",
           "-f", "rawvideo", "-vcodec", "rawvideo",
           "-s", f"{width}x{height}", "-pix_fmt", "rgb24",
           "-r", str(fps), "-i", "-",
           "-vf", "vflip",          # 原始缓冲区是自下而上的行序
           "-an", "-c:v", "libx264", "-preset", "veryfast",
           "-crf", str(ns.crf), "-pix_fmt", "yuv420p",
           str(video_path)]
    encoder = subprocess.Popen(cmd, stdin=subprocess.PIPE)

    log_fh = open(log_path, "w", encoding="utf-8")
    env = dict(os.environ, PYTHONIOENCODING="utf-8", PYTHONUNBUFFERED="1")
    runner_cmd = [sys.executable, *_runner_command(ns.stage, ns),
                  "--host", ns.host, "--port", str(ns.port),
                  "--out-dir", str(ROOT / "docs" / "implementation" / "runs"),
                  "--log", str(log_path)]
    runner = subprocess.Popen(runner_cmd, cwd=str(ROOT), env=env,
                              stdout=log_fh, stderr=subprocess.STDOUT)

    sensor = None
    frames = 0
    duplicates = 0
    first_t = last_t = None
    missed_slots = 0
    error = None
    run_started = False
    start_deadline = time.time() + ns.start_timeout
    hard_deadline = time.time() + ns.max_wall
    stop_since = None
    progress_t = None
    progress_since = time.time()

    try:
        # ── 等 runner 完成加载/校验并开始 stepping ──────────────
        while time.time() < start_deadline:
            if runner.poll() is not None:
                error = f"runner 在启动仿真前退出 (code={runner.returncode})"
                break
            state = int(sim.getSimulationState())
            if state != sim.simulation_stopped:
                run_started = True
                break
            time.sleep(0.1)
        if not run_started and error is None:
            error = f"等待 {ns.start_timeout:.0f}s 后仿真仍未启动"

        # ── 仿真启动后才创建相机, 不干扰启动点场景校验 ──────────
        if error is None:
            pos = tuple(ns.cam_pos) if ns.cam_pos else preset["cam_pos"]
            target = tuple(ns.cam_target) if ns.cam_target else preset["cam_target"]
            fov = float(ns.fov if ns.fov is not None else preset["fov"])
            sensor = sim.createVisionSensor(
                2 | 4, [width, height, 0, 0],
                [0.01, 30.0, math.radians(fov), 0.01, 0, 0,
                 0.5, 0.5, 0.5, 0, 0])
            sim.setObjectAlias(sensor, "RecordingCamera")
            sim.setObjectMatrix(sensor, look_at_matrix(pos, target),
                                sim.handle_world)
            print(f"[REC] 相机已创建: pos={pos} target={target} "
                  f"fov={fov}° {width}x{height}@{fps:g}fps "
                  f"sample_dt={sample_dt}s")

            next_t = None
            while time.time() < hard_deadline:
                state = int(sim.getSimulationState())
                t = float(sim.getSimulationTime())
                now = time.time()

                if state == sim.simulation_stopped:
                    if stop_since is None:
                        stop_since = now
                    elif now - stop_since > 3.0:
                        break
                else:
                    stop_since = None

                if next_t is None:
                    # 相机在仿真已运行时才创建; 等引擎完成一次 sensing 再抓帧,
                    # 否则第一帧是未渲染的全黑图像.
                    next_t = t + sample_dt
                if progress_t is None or t > progress_t + 1e-9:
                    progress_t = t
                    progress_since = now
                elif (state != sim.simulation_stopped
                      and now - progress_since > ns.freeze_timeout):
                    raise RuntimeError(
                        f"仿真 {ns.freeze_timeout:.0f}s 未推进 "
                        f"(sim_time={t:.3f}, state={state}) — 疑似冻结")

                if state != sim.simulation_stopped and t >= next_t - 1e-9:
                    img, res = sim.getVisionSensorImg(sensor)
                    if list(res) != [width, height]:
                        raise RuntimeError(
                            f"图像分辨率 {res} 与请求 {width}x{height} 不符")
                    encoder.stdin.write(img)
                    frames += 1
                    if first_t is None:
                        first_t = t
                    last_t = t

                    # 追上被跳过的采样槽: 复制当前帧, 保证固定时间映射
                    slots = int(math.floor((t - next_t) / sample_dt + 1e-9)) + 1
                    next_t += slots * sample_dt
                    extra = min(slots - 1, 20)
                    for _ in range(extra):
                        encoder.stdin.write(img)
                        frames += 1
                        duplicates += 1
                    missed_slots += slots - 1 - extra

                # runner 结束但仿真状态未及时回到 stopped 时兜底
                if runner.poll() is not None and state == sim.simulation_stopped:
                    break
                # 正常推进时 20 Hz 轮询; 长时间规划/计算 (sim_time 不变) 时
                # 退避到 2 Hz, 减少与 runner 的 IK/碰撞查询争用 ZMQ 服务.
                idle = now - progress_since
                time.sleep(0.05 if idle < 3.0 else 0.5)
            else:
                error = f"超过最大录制时长 {ns.max_wall:.0f}s"

            if frames == 0 and error is None:
                error = "没有采到任何帧"

    except Exception as exc:                       # noqa: BLE001
        error = f"{type(exc).__name__}: {exc}"
    finally:
        try:
            if encoder.stdin:
                encoder.stdin.close()
        except Exception:
            pass
        encode_rc = encoder.wait(timeout=120)
        if encode_rc != 0 and error is None:
            error = f"ffmpeg 退出码 {encode_rc}"

        # 录制被中断时确保 runner 能收到停止信号并自行收尾
        try:
            if (runner.poll() is None
                    and int(sim.getSimulationState())
                    != sim.simulation_stopped):
                sim.stopSimulation()
        except Exception:
            pass

        # runner 未结束则等待; runner 已结束则直接收尾
        try:
            runner_rc = runner.wait(timeout=ns.runner_timeout)
        except subprocess.TimeoutExpired:
            runner.terminate()
            try:
                runner_rc = runner.wait(timeout=15)
            except subprocess.TimeoutExpired:
                runner.kill()
                runner_rc = runner.wait()
            if error is None:
                error = "runner 收尾超时被终止"
        log_fh.close()

        # runner 已退出后再移除录制相机, 避免影响其收尾检查
        if sensor is not None:
            try:
                sim.removeObjects([sensor])
            except Exception:
                pass

        sim_s = (last_t - first_t) if (first_t is not None) else 0.0
        video_s = frames / fps if fps else 0.0
        meta = {
            "stage": ns.stage,
            "mode": ns.mode,
            "target": ns.target or None,
            "scenario": ns.scenario or None,
            "fault": ns.fault,
            "recorded_at": datetime.now().astimezone().isoformat(
                timespec="seconds"),
            "artifact": "current",
            "video": str(video_path),
            "video_seconds": round(video_s, 3),
            "sim_seconds": round(sim_s, 3),
            "playback_speed": (round(sim_s / video_s, 3)
                               if video_s > 0 else None),
            "frames": frames,
            "duplicated_frames": duplicates,
            "missed_sample_slots": missed_slots,
            "fps": fps,
            "sample_dt": sample_dt,
            "resolution": [width, height],
            "camera": {
                "position": list(ns.cam_pos or preset["cam_pos"]),
                "target": list(ns.cam_target or preset["cam_target"]),
                "fov_deg": ns.fov if ns.fov is not None else preset["fov"],
            },
            "runner_exit_code": runner_rc,
            "runner_log": str(log_path),
            "error": error,
        }
        meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=2),
                             encoding="utf-8")
        if error is not None and video_path.exists() and frames == 0:
            try:
                video_path.unlink()
            except OSError:
                pass
        return meta


# ═══════════════════════════════════════════════════════════════
# CLI
# ═══════════════════════════════════════════════════════════════

def main(argv=None) -> int:
    try:                      # Windows 控制台默认代码页会让中文输出乱码
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass
    ap = argparse.ArgumentParser(description="录制阶段仿真运行过程 (MP4)")
    ap.add_argument("--stage", type=int, choices=(1, 2, 3, 4), required=True)
    ap.add_argument("--target", default=None,
                    help="stage1/2 的 target (默认 station)")
    ap.add_argument("--scenario", default=None,
                    help="stage3/4 的 scenario (stage3 默认 closed_loop, "
                         "stage4 默认 full_flow)")
    ap.add_argument("--fault", default="none",
                    help="阶段一/二可注入故障 (如 ik_unreachable / "
                         "runtime_obstacle); stage3/4 场景已含默认故障")
    ap.add_argument("--time", type=float, default=None,
                    help="单次运行最大仿真时间 (s), 默认按阶段取值")
    ap.add_argument("--out", default=None,
                    help="输出 MP4 路径; 默认 docs/implementation/runs/"
                         "recordings/stageN_<mode>_<stamp>.mp4")
    ap.add_argument("--out-dir", default=str(DEFAULT_OUT_DIR),
                    help="未指定 --out 时的输出目录")
    ap.add_argument("--width", type=int, default=1280)
    ap.add_argument("--height", type=int, default=720)
    ap.add_argument("--fps", type=float, default=20.0)
    ap.add_argument("--sample-dt", type=float, default=None,
                    help="每帧对应的仿真时间 (s); 缺省按阶段预设")
    ap.add_argument("--crf", type=int, default=20)
    ap.add_argument("--fov", type=float, default=None,
                    help="覆盖阶段预设视场角 (度)")
    ap.add_argument("--cam-pos", type=float, nargs=3, default=None,
                    metavar=("X", "Y", "Z"), help="覆盖相机位置")
    ap.add_argument("--cam-target", type=float, nargs=3, default=None,
                    metavar=("X", "Y", "Z"), help="覆盖相机目标点")
    ap.add_argument("--host", default="localhost")
    ap.add_argument("--port", type=int, default=23000)
    ap.add_argument("--start-timeout", type=float, default=900.0,
                    help="等待 runner 启动仿真的上限 (s); 阶段四首次加载较慢")
    ap.add_argument("--max-wall", type=float, default=5400.0,
                    help="录制循环的墙钟上限 (s)")
    ap.add_argument("--freeze-timeout", type=float, default=180.0,
                    help="仿真时间停滞超过该秒数则判定冻结并中止 (s)")
    ap.add_argument("--runner-timeout", type=float, default=600.0,
                    help="录制结束后等待 runner 收尾的上限 (s)")
    ns = ap.parse_args(argv)

    if ns.stage <= 2:
        ns.target = ns.target or "station"
        if ns.target not in ("station", "agv", "both"):
            ap.error("--target 只能是 station/agv/both")
        ns.scenario = ""
        mode = ns.target
        if ns.fault and ns.fault != "none":
            mode = f"{mode}_fault_{ns.fault}"
    else:
        ns.scenario = ns.scenario or (
            "closed_loop" if ns.stage == 3 else "full_flow")
        ns.target = ""
        mode = ns.scenario
        if ns.fault and ns.fault != "none":
            mode = f"{mode}_fault_{ns.fault}"
    ns.mode = mode
    if ns.time is None:
        ns.time = _default_time(ns.stage, ns.target, ns.scenario)

    preset = _preset_for(ns.stage, ns.target, ns.scenario, ns.fault)
    preset["label"] = mode
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    if ns.out:
        video_path = Path(ns.out).resolve()
    else:
        video_path = (Path(ns.out_dir) /
                      f"stage{ns.stage}_{mode}_{stamp}.mp4").resolve()
    log_path = video_path.with_suffix(".log")
    meta_path = video_path.with_suffix(".json")

    try:
        sim = connect(ns.host, ns.port)
    except Exception as exc:                       # noqa: BLE001
        print(f"[ERR] 连接 CoppeliaSim 失败 ({ns.host}:{ns.port}): {exc}",
              file=sys.stderr)
        return 2
    if not wait_stopped(sim):
        print("[ERR] 仿真未处于停止态; 请先 stop 当前运行再录制",
              file=sys.stderr)
        return 2

    print(f"[REC] stage {ns.stage} / {mode}: {video_path}")
    meta = record(sim, ns, preset, video_path, log_path, meta_path)
    print(json.dumps(meta, ensure_ascii=False, indent=2))
    return 0 if (meta["error"] is None and meta["frames"] > 0) else 1


if __name__ == "__main__":
    raise SystemExit(main())
