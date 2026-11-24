"""recording.py — 运行期仿真视频录制 (可选, 不影响运行语义).

录制是运行级旁路: worker/CLI 在启动点校验通过、进入 stepping 之后才
创建临时 vision sensor, 按固定仿真时间间隔抽帧, 交给 ffmpeg 编码为
H.264 MP4. 相机不进入场景工件, 运行结束/异常时移除; 录制失败只把
``status`` 标记为 ``failed``/``partial``, 不改变运行结果和退出码.

约束:
  - 录制器只在持有 stepping 的线程内调用, 不另开 ZMQ 连接;
  - 输出固定写到 ``spec.RECORDING_DIR`` 或显式 ``--record-dir``, 不得
    写入场景工件目录;
  - 视频与元数据按 ``run_id`` 命名, 与运行报告/运行记录对应.

版本 ``spec.RECORDING_VERSION`` 只随抽帧/编码/元数据语义变化递增.
"""

from __future__ import annotations

import json
import logging
import math
import re
import subprocess
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Callable, Optional

from .. import spec

LOGGER = logging.getLogger("flexible_factory.recording")
DEFAULT_SIZE = "1280x720"
DEFAULT_FPS = 20.0
DEFAULT_SAMPLE_DT = 0.1
DEFAULT_FOV_DEG = 52.0
_MAX_DUPLICATE_FRAMES = 20

Size = tuple[int, int]


class RecordingError(RuntimeError):
    """录制不可用或启动失败 (显式请求时用于快速失败)."""


def parse_size(text: str | Size) -> Size:
    """解析 ``1280x720`` (也接受 ``1280*720``); 非法值抛 ValueError."""
    if isinstance(text, (tuple, list)) and len(text) == 2:
        width, height = int(text[0]), int(text[1])
    else:
        match = re.fullmatch(r"\s*(\d+)\s*[xX*]\s*(\d+)\s*", str(text))
        if not match:
            raise ValueError(f"录制分辨率必须形如 1280x720: {text!r}")
        width, height = int(match.group(1)), int(match.group(2))
    if not (16 <= width <= 3840 and 16 <= height <= 2160):
        raise ValueError(f"录制分辨率超出范围: {width}x{height}")
    if width % 2 or height % 2:
        raise ValueError(f"录制分辨率必须为偶数: {width}x{height}")
    return width, height


@dataclass(frozen=True)
class RecordingOptions:
    """一次运行的录制选项; ``enabled=False`` 时不创建任何对象."""

    enabled: bool = False
    out_dir: Path = spec.RECORDING_DIR
    width: int = 1280
    height: int = 720
    fps: float = DEFAULT_FPS
    sample_dt: float = DEFAULT_SAMPLE_DT

    def validate(self) -> None:
        if self.width % 2 or self.height % 2:
            raise ValueError("录制分辨率必须为偶数")
        if not (16 <= self.width <= 3840 and 16 <= self.height <= 2160):
            raise ValueError("录制分辨率超出范围")
        if not (1.0 <= float(self.fps) <= 60.0):
            raise ValueError("录制帧率必须在 1–60 fps")
        if not (0.02 <= float(self.sample_dt) <= 5.0):
            raise ValueError("录制抽帧间隔必须在 0.02–5 s")
        out_dir = Path(self.out_dir).resolve()
        artifact_root = Path(spec.ARTIFACT_ROOT).resolve()
        if out_dir == artifact_root or out_dir.is_relative_to(artifact_root):
            raise ValueError("录像目录不得位于场景工件目录内")

    @property
    def resolution(self) -> str:
        return f"{self.width}x{self.height}"


def options_from_cli(*, record: str, out_dir: str, size: str,
                     sample_dt: float) -> RecordingOptions:
    width, height = parse_size(size)
    options = RecordingOptions(
        enabled=str(record) == "on", out_dir=Path(out_dir),
        width=width, height=height, sample_dt=float(sample_dt))
    options.validate()
    return options


def ffmpeg_executable() -> str:
    try:
        import imageio_ffmpeg
    except ImportError as exc:      # pragma: no cover - 环境缺依赖
        raise RecordingError(
            "录制需要 imageio-ffmpeg; 请执行 uv sync") from exc
    return imageio_ffmpeg.get_ffmpeg_exe()


def ensure_available() -> None:
    """显式请求录制时在连接仿真前快速失败."""
    if not Path(ffmpeg_executable()).is_file():   # pragma: no cover
        raise RecordingError("未找到 ffmpeg 可执行文件")


def camera_view(layout=None) -> tuple[tuple[float, float, float],
                                      tuple[float, float, float], float]:
    """按场景地面范围推导东南高位机位; 布局缺失时用 L5_UFLOW 默认地面."""
    floor = getattr(layout, "floor_rect", None) or (-4.0, -3.0, 4.0, 3.0)
    x0, y0, x1, y1 = [float(v) for v in floor]
    width = max(x1 - x0, 1.0)
    height = max(y1 - y0, 1.0)
    cx, cy = (x0 + x1) / 2.0, (y0 + y1) / 2.0
    position = (round(cx + 0.35 * width, 4),
                round(cy - 0.80 * height, 4),
                round(max(2.4, 0.55 * height), 4))
    target = (round(cx + 0.13 * width, 4),
              round(cy - 0.01 * height, 4), 0.35)
    return position, target, DEFAULT_FOV_DEG


def _normalize(vector) -> tuple[float, float, float]:
    norm = math.sqrt(sum(value * value for value in vector))
    return tuple(value / norm for value in vector)


def _cross(a, b) -> tuple[float, float, float]:
    return (a[1] * b[2] - a[2] * b[1],
            a[2] * b[0] - a[0] * b[2],
            a[0] * b[1] - a[1] * b[0])


def look_at_matrix(position, target) -> list[float]:
    """Z 向上的相机位姿 (sim.setObjectMatrix 的 3x4)."""
    forward = _normalize(tuple(target[i] - position[i] for i in range(3)))
    right = _normalize(_cross((0.0, 0.0, 1.0), forward))
    up = _cross(forward, right)
    return [right[0], up[0], forward[0], position[0],
            right[1], up[1], forward[1], position[1],
            right[2], up[2], forward[2], position[2]]


class FFmpegEncoder:
    """把 rgb24 原始帧流写入 libx264 MP4; 视觉传感器缓冲区是自下而上."""

    def __init__(self, path: Path, width: int, height: int,
                 fps: float, *, crf: int = 20) -> None:
        self.path = Path(path)
        self.width, self.height = int(width), int(height)
        self.frame_bytes = self.width * self.height * 3
        command = [
            ffmpeg_executable(), "-y", "-loglevel", "error",
            "-f", "rawvideo", "-vcodec", "rawvideo",
            "-s", f"{self.width}x{self.height}", "-pix_fmt", "rgb24",
            "-r", str(float(fps)), "-i", "-",
            "-vf", "vflip", "-an",
            "-c:v", "libx264", "-preset", "veryfast", "-crf", str(int(crf)),
            "-pix_fmt", "yuv420p", str(self.path),
        ]
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._process = subprocess.Popen(
            command, stdin=subprocess.PIPE,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        self._closed = False

    def write(self, frame: bytes) -> None:
        if self._closed:
            raise RecordingError("编码器已关闭")
        if len(frame) != self.frame_bytes:
            raise RecordingError(
                f"帧大小 {len(frame)} != {self.frame_bytes}")
        self._process.stdin.write(frame)

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        try:
            if self._process.stdin is not None:
                self._process.stdin.close()
        finally:
            code = self._process.wait(timeout=120)
        if code != 0:
            raise RecordingError(f"ffmpeg 退出码 {code}")


class SimulationRecorder:
    """单次运行的录制器; 所有方法都不向调用方抛异常 (``start`` 除外)."""

    def __init__(self, sim, run_id: str, options: RecordingOptions, *,
                 layout=None, logger: Optional[logging.Logger] = None,
                 encoder_factory: Optional[Callable[..., object]] = None
                 ) -> None:
        self.sim = sim
        self.run_id = _safe_name(run_id)
        self.options = options
        self.layout = layout
        self.logger = logger or LOGGER
        self._encoder_factory = encoder_factory or FFmpegEncoder
        self._encoder = None
        self._sensor = None
        self._closed = False
        self._status = "idle"
        self._error = ""
        self._frames = 0
        self._duplicates = 0
        self._first_t: Optional[float] = None
        self._last_t: Optional[float] = None
        self._next_t: Optional[float] = None
        self._started_at = ""

    # ── 生命周期 ────────────────────────────────────────────

    def start(self) -> bool:
        """在 stepping 之后创建相机; 失败时记录状态并返回 False."""
        if self._closed or self._encoder is not None:
            return self._status == "recording"
        self._started_at = _now()
        try:
            self.options.validate()
            Path(self.options.out_dir).mkdir(parents=True, exist_ok=True)
            self._encoder = self._encoder_factory(
                self.path, self.options.width, self.options.height,
                self.options.fps)
            self._sensor = self._create_camera()
            self._status = "recording"
            return True
        except Exception as exc:        # noqa: BLE001 - 录制不阻断运行
            self._fail(exc)
            return False

    def capture_if_due(self) -> None:
        if self._status != "recording" or self._closed:
            return
        try:
            sim_time = float(self.sim.getSimulationTime())
            if self._next_t is None:
                self._next_t = sim_time + self.options.sample_dt
                return
            if sim_time < self._next_t - 1e-9:
                return
            image, resolution = self.sim.getVisionSensorImg(self._sensor)
            if [int(v) for v in resolution] != [self.options.width,
                                                self.options.height]:
                raise RecordingError(
                    f"图像分辨率 {resolution} != "
                    f"{self.options.resolution}")
            self._encoder.write(image)
            self._frames += 1
            if self._first_t is None:
                self._first_t = sim_time
            self._last_t = sim_time
            slots = int(math.floor(
                (sim_time - self._next_t) / self.options.sample_dt + 1e-9)) + 1
            self._next_t += slots * self.options.sample_dt
            for _ in range(min(slots - 1, _MAX_DUPLICATE_FRAMES)):
                self._encoder.write(image)
                self._frames += 1
                self._duplicates += 1
        except Exception as exc:        # noqa: BLE001
            self._fail(exc)

    def close(self, *, error: str = "") -> dict:
        """收尾并返回元数据; 可重复调用, 永不抛异常."""
        if self._closed:
            return self.meta()
        self._closed = True
        encode_error = ""
        try:
            if self._encoder is not None:
                self._encoder.close()
        except Exception as exc:        # noqa: BLE001
            encode_error = f"{type(exc).__name__}: {exc}"
        self._remove_camera()
        if self._error:
            status = "failed"
        elif encode_error:
            status = "failed"
            self._error = encode_error
        elif self._frames == 0:
            status = "failed"
            self._error = "没有采集到帧"
        elif error:
            status = "partial"
            self._error = str(error)
        else:
            status = "ok"
        self._status = status
        if status == "failed" and self._frames == 0:
            try:
                self.path.unlink(missing_ok=True)
            except OSError:
                pass
        meta = self.meta()
        try:
            self.meta_path.write_text(
                json.dumps(meta, ensure_ascii=False, indent=2),
                encoding="utf-8")
        except OSError as exc:
            self.logger.warning("[RECORDING] 元数据写入失败: %s", exc)
        if status == "failed":
            self.logger.warning("[RECORDING] run=%s 录制失败: %s",
                                self.run_id, self._error)
        else:
            self.logger.info("[RECORDING] run=%s %s frames=%s path=%s",
                             self.run_id, status, self._frames, self.path)
        return meta

    # ── 查询 ────────────────────────────────────────────────

    @property
    def recording(self) -> bool:
        return self._status == "recording"

    @property
    def path(self) -> Path:
        return Path(self.options.out_dir) / f"{self.run_id}.mp4"

    @property
    def meta_path(self) -> Path:
        return self.path.with_suffix(".json")

    def meta(self) -> dict:
        sim_seconds = ((self._last_t - self._first_t)
                       if self._first_t is not None else 0.0)
        video_seconds = self._frames / self.options.fps
        return {
            "version": spec.RECORDING_VERSION,
            "run_id": self.run_id,
            "status": self._status,
            "path": str(self.path.resolve()),
            "meta_path": str(self.meta_path.resolve()),
            "frames": int(self._frames),
            "duplicated_frames": int(self._duplicates),
            "sim_seconds": round(float(sim_seconds), 3),
            "video_seconds": round(float(video_seconds), 3),
            "playback_speed": (round(sim_seconds / video_seconds, 3)
                               if video_seconds > 0 else None),
            "fps": float(self.options.fps),
            "sample_dt": float(self.options.sample_dt),
            "resolution": [self.options.width, self.options.height],
            "camera": self._camera_meta(),
            "started_at": self._started_at,
            "finished_at": "" if not self._closed else _now(),
            "error": self._error,
        }

    def status(self) -> dict:
        return {
            "status": self._status,
            "frames": int(self._frames),
            "path": str(self.path.resolve()),
            "error": self._error,
        }

    # ── 内部 ────────────────────────────────────────────────

    def _camera_meta(self) -> dict:
        position, target, fov = camera_view(self.layout)
        return {"position": list(position), "target": list(target),
                "fov_deg": fov}

    def _create_camera(self) -> int:
        position, target, fov = camera_view(self.layout)
        sensor = int(self.sim.createVisionSensor(
            2 | 4, [self.options.width, self.options.height, 0, 0],
            [0.01, 30.0, math.radians(fov), 0.01, 0, 0,
             0.5, 0.5, 0.5, 0, 0]))
        self.sim.setObjectAlias(sensor, "RecordingCamera")
        self.sim.setObjectMatrix(sensor, look_at_matrix(position, target),
                                 self.sim.handle_world)
        return sensor

    def _remove_camera(self) -> None:
        if self._sensor is None:
            return
        try:
            self.sim.removeObjects([self._sensor])
        except Exception:               # noqa: BLE001
            pass
        self._sensor = None

    def _fail(self, exc: Exception) -> None:
        self._status = "failed"
        self._error = f"{type(exc).__name__}: {exc}"
        self.logger.warning("[RECORDING] run=%s 录制中止: %s",
                            self.run_id, self._error)
        try:
            if self._encoder is not None:
                self._encoder.close()
        except Exception:               # noqa: BLE001
            pass


def make_recorder(sim, run_id: str, options: Optional[RecordingOptions], *,
                  layout=None, logger: Optional[logging.Logger] = None,
                  encoder_factory: Optional[Callable[..., object]] = None):
    """按选项构造录制器; 未启用时返回 None (零开销)."""
    if options is None or not options.enabled:
        return None
    return SimulationRecorder(
        sim, run_id, options, layout=layout, logger=logger,
        encoder_factory=encoder_factory)


def _safe_name(value: str) -> str:
    name = re.sub(r"[^A-Za-z0-9._-]+", "_", str(value or "run")).strip("._")
    return (name or "run")[:100]


def _now() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


__all__ = [
    "DEFAULT_SAMPLE_DT", "DEFAULT_SIZE", "FFmpegEncoder", "RecordingError",
    "RecordingOptions", "SimulationRecorder", "camera_view", "ensure_available",
    "ffmpeg_executable", "look_at_matrix", "make_recorder", "options_from_cli",
    "parse_size",
]