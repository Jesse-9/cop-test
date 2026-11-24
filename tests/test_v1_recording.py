"""运行录像的纯逻辑/本地 HTTP 测试 (不连接 CoppeliaSim)."""

from __future__ import annotations

import json
import threading
from pathlib import Path
from types import SimpleNamespace
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import pytest

from flexible_factory import spec
from flexible_factory.api.server import OrderApiServer
from flexible_factory.application.orders import OrderRunCommand
from flexible_factory.application.recording import (
    FFmpegEncoder, RecordingOptions, SimulationRecorder, camera_view,
    make_recorder, options_from_cli, parse_size)
from flexible_factory.persistence.errors import OrderStateError


class FakeSim:
    handle_world = 0

    def __init__(self, width: int, height: int) -> None:
        self.width, self.height = width, height
        self.time = 5.0
        self.sensor = None
        self.removed: list[int] = []
        self.images = 0

    def getSimulationTime(self) -> float:
        return self.time

    def createVisionSensor(self, options, ints, floats) -> int:
        self.sensor = 42
        return 42

    def setObjectAlias(self, handle, alias) -> None:
        self.alias = alias

    def setObjectMatrix(self, handle, matrix, ref) -> None:
        self.matrix = matrix

    def getVisionSensorImg(self, handle):
        self.images += 1
        frame = b"\x00" * (self.width * self.height * 3)
        return frame, [self.width, self.height]

    def removeObjects(self, handles) -> None:
        self.removed.extend(handles)


class FakeEncoder:
    def __init__(self, path, width, height, fps) -> None:
        self.path = Path(path)
        self.width, self.height = width, height
        self.frames: list[int] = []
        self.closed = False

    def write(self, frame: bytes) -> None:
        self.frames.append(len(frame))

    def close(self) -> None:
        self.closed = True


class FailingEncoder(FakeEncoder):
    def write(self, frame: bytes) -> None:
        raise RuntimeError("encoder down")


def _options(tmp_path: Path, **overrides) -> RecordingOptions:
    values = dict(enabled=True, out_dir=tmp_path, width=16, height=16,
                  fps=20.0, sample_dt=0.1)
    values.update(overrides)
    return RecordingOptions(**values)


def test_parse_size_accepts_x_and_star():
    assert parse_size("1280x720") == (1280, 720)
    assert parse_size("640*360") == (640, 360)
    assert parse_size((320, 240)) == (320, 240)


@pytest.mark.parametrize("value", ["1280", "1280x", "1x2", "1281x720",
                                   "10000x720", "abc"])
def test_parse_size_rejects_invalid(value):
    with pytest.raises(ValueError):
        parse_size(value)


def test_options_from_cli_validates_sample_dt():
    options = options_from_cli(record="on", out_dir="var/recordings",
                               size="640x360", sample_dt=0.2)
    assert options.enabled is True
    assert options.resolution == "640x360"
    with pytest.raises(ValueError):
        options_from_cli(record="on", out_dir="var/recordings",
                         size="640x360", sample_dt=0.0)


def test_options_reject_scene_artifact_directory():
    options = _options(spec.ARTIFACT_ROOT / "recording")
    with pytest.raises(ValueError, match="场景工件目录"):
        options.validate()


def test_camera_view_matches_l5_uflow_preset():
    layout = SimpleNamespace(floor_rect=(-4.0, -3.0, 4.0, 3.0))
    position, target, fov = camera_view(layout)
    assert position == (2.8, -4.8, 3.3)
    assert target == (1.04, -0.06, 0.35)
    assert fov == 52.0


def test_disabled_options_create_no_recorder(tmp_path):
    assert make_recorder(FakeSim(16, 16), "run-0",
                         RecordingOptions(enabled=False, out_dir=tmp_path)
                         ) is None


def test_recorder_captures_on_schedule_and_closes(tmp_path):
    sim = FakeSim(16, 16)
    options = _options(tmp_path)
    recorder = make_recorder(sim, "run-1", options,
                             encoder_factory=FakeEncoder)
    assert recorder is not None and recorder.start() is True
    assert sim.sensor == 42 and sim.alias == "RecordingCamera"

    recorder.capture_if_due()          # 首次调用只排定下一帧
    assert recorder.meta()["frames"] == 0
    sim.time = 5.15
    recorder.capture_if_due()
    sim.time = 5.35                    # 跳过 5.25 槽位, 用当前帧补齐
    recorder.capture_if_due()
    meta = recorder.close()

    assert meta["status"] == "ok"
    assert meta["frames"] == 3
    assert meta["duplicated_frames"] == 1
    assert meta["sim_seconds"] == pytest.approx(0.2)
    assert sim.removed == [42]
    saved = json.loads(recorder.meta_path.read_text(encoding="utf-8"))
    assert saved["status"] == "ok" and saved["frames"] == 3


def test_recorder_marks_partial_when_run_aborts(tmp_path):
    sim = FakeSim(16, 16)
    recorder = make_recorder(sim, "run-2", _options(tmp_path),
                             encoder_factory=FakeEncoder)
    recorder.start()
    recorder.capture_if_due()
    sim.time = 5.15
    recorder.capture_if_due()
    meta = recorder.close(error="RuntimeError: boom")
    assert meta["status"] == "partial"
    assert "boom" in meta["error"]


def test_recorder_failure_does_not_raise(tmp_path):
    sim = FakeSim(16, 16)
    recorder = make_recorder(sim, "run-3", _options(tmp_path),
                             encoder_factory=FailingEncoder)
    assert recorder.start() is True
    recorder.capture_if_due()
    sim.time = 5.15
    recorder.capture_if_due()          # 内部失败, 不外抛
    assert recorder.status()["status"] == "failed"
    meta = recorder.close()
    assert meta["status"] == "failed" and meta["frames"] == 0


def test_ffmpeg_encoder_writes_playable_file(tmp_path):
    pytest.importorskip("imageio_ffmpeg")
    path = tmp_path / "video.mp4"
    encoder = FFmpegEncoder(path, 16, 16, 20.0)
    frame = b"\x80" * (16 * 16 * 3)
    encoder.write(frame)
    encoder.write(frame)
    encoder.close()
    assert path.is_file() and path.stat().st_size > 0


def test_order_run_command_reports_record_flag():
    command = OrderRunCommand(
        order=SimpleNamespace(order_id="o", version=1),
        compiled=SimpleNamespace(digest="d"), run_id="r",
        artifact_id="a", record=True)
    assert command.as_dict()["record"] is True


class _StubVideoService:
    def __init__(self, path: Path | None, *, missing: bool = False) -> None:
        self.path = path
        self.missing = missing
        self.requested: list[str] = []

    def video_path(self, run_id: str) -> Path:
        self.requested.append(run_id)
        if self.missing or self.path is None:
            raise OrderStateError("没有录像")
        return self.path


def _video_server(service) -> OrderApiServer:
    return OrderApiServer(service, host="127.0.0.1", port=0)


def _get(url: str, headers: dict | None = None):
    request = Request(url, headers=headers or {})
    return urlopen(request, timeout=5)


def test_video_endpoint_serves_range(tmp_path):
    path = tmp_path / "run.mp4"
    path.write_bytes(b"0123456789")
    service = _StubVideoService(path)
    server = _video_server(service)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        with _get(f"{server.base_url}/api/runs/r1/video",
                  {"Range": "bytes=2-5"}) as response:
            assert response.status == 206
            assert response.headers["Content-Range"] == "bytes 2-5/10"
            assert response.read() == b"2345"
        with _get(f"{server.base_url}/api/runs/r1/video") as response:
            assert response.status == 200
            assert response.read() == b"0123456789"
        assert service.requested == ["r1", "r1"]
    finally:
        server.shutdown()


def test_video_endpoint_reports_missing_recording(tmp_path):
    server = _video_server(_StubVideoService(None, missing=True))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        with pytest.raises(HTTPError) as excinfo:
            _get(f"{server.base_url}/api/runs/r2/video")
        assert excinfo.value.code == 409
        payload = json.loads(excinfo.value.read().decode("utf-8"))
        assert payload["error"]["code"] == "VIDEO_NOT_READY"
    finally:
        server.shutdown()