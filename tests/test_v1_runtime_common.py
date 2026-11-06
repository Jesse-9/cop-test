"""共享运行时装配测试.

用假影子臂/碰撞运行时验证 v1 与双站层共用的单臂 builder:
  - 启动摘要字段与顺序 (Stage3/v1 的证据契约);
  - 场景级差异只通过入参传入, 不由 builder 内部分支;
  - 构建失败时清理本臂半成品.
不启动 CoppeliaSim.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from flexible_factory.simulation import config_motion as movecfg
from flexible_factory.simulation import runtime, runtime_common, runtime_dual


class _Sim:
    simulation_stopped = 0

    def __init__(self) -> None:
        self.positions = []

    def getSimulationState(self):
        return 4

    def getJointPosition(self, joint):
        return 0.0

    def getShapeBB(self, handle):
        return [movecfg.CUP_RADIUS_DESIGN * 2.0,
                movecfg.CUP_RADIUS_DESIGN * 2.0, 0.01]


class _Shadow:
    links = [11, 12]
    link_aliases = ["link_a", "link_b"]
    pad_body = 21
    load = 22
    visibility_applied = True
    alias_names = ["Shadow_UR5_01"]

    def as_dict(self):
        return {"root_alias": "Shadow_UR5_01"}


class _Probe:
    instances = []

    def __init__(self, sim, shadow) -> None:
        self.sim = sim
        self.shadow = shadow
        self.destroyed = False
        _Probe.instances.append(self)

    def set_configuration(self, joints) -> None:
        self.joints = list(joints)

    def verify_fk(self, view):
        return {"ok": True}

    def destroy(self):
        self.destroyed = True
        return [self.shadow.pad_body]


class _Spec:
    items = (1, 2, 3)


class _Geometry:
    excluded = [{"alias": "coarse"}]
    workcell_aliases = ["workcell_a"]
    workcell = [31, 32]


class _Collisions:
    instances = []
    fail_build = False
    semantics = {"ok": True}

    def __init__(self) -> None:
        self.collections = {"self": None, "workcell": None}
        self.specs = [_Spec(), _Spec()]
        self._self_pairs = [(11, 12)]
        self.geometry = _Geometry()
        self.destroyed = False
        _Collisions.instances.append(self)

    @classmethod
    def build(cls, *args, **kwargs):
        if cls.fail_build:
            raise RuntimeError("collision build failed")
        return cls()

    def query_semantics_probe(self):
        return dict(self.semantics)

    def destroy(self) -> None:
        self.destroyed = True


class _Checker:
    def __init__(self, sim, probe, collisions, cup_radius=None) -> None:
        self.cup_radius = cup_radius


class _Planner:
    def __init__(self, checker, ik) -> None:
        self.checker = checker
        self.ik = ik


class _Safety:
    def __init__(self, logger=None) -> None:
        self.logger = logger


def _patch(monkeypatch, *, collisions=None):
    monkeypatch.setattr(runtime_common, "build_shadow_arm",
                        lambda *args, **kwargs: _Shadow())
    monkeypatch.setattr(runtime_common, "ShadowProbe", _Probe)
    monkeypatch.setattr(runtime_common, "CollisionRuntime", collisions or _Collisions)
    monkeypatch.setattr(runtime_common, "ConfigurationChecker", _Checker)
    monkeypatch.setattr(runtime_common, "LocalPlanner", _Planner)
    monkeypatch.setattr(runtime_common, "Stage2Safety", _Safety)


def _view():
    return SimpleNamespace(
        unit_id="A",
        unit=SimpleNamespace(
            spec=SimpleNamespace(arm_base=(0.0, 0.0, 0.0)),
            robot_joints=[1, 2, 3, 4, 5, 6],
            aliases={"island": "Island_A"}))


def _build(monkeypatch, *, fail_build=False, semantics=None, **overrides):
    _Probe.instances = []
    _Collisions.instances = []
    _Collisions.fail_build = fail_build
    _Collisions.semantics = ({"ok": True} if semantics is None
                             else dict(semantics))
    _patch(monkeypatch)
    kwargs = dict(
        view=_view(), shadow_aliases={"arm": "Shadow_UR5_01"},
        station_aliases=("Island_A_Station",), obstacles=[41],
        obstacle_info={"other_unit": "B"}, obstacle_key="other_unit",
        ik=SimpleNamespace(name="ik"))
    kwargs.update(overrides)
    return runtime_common.build_arm_runtime(_Sim(), **kwargs)


def test_builders_reject_running_simulation():
    sim = _Sim()
    with pytest.raises(runtime.Stage3RuntimeBuildError):
        runtime.build_stage3_runtime(sim, scene=None, ik_by_unit={})
    with pytest.raises(runtime.RuntimeBuildError):
        runtime.build_v1_runtime(sim, scene=None, ik_by_unit={})


def test_runtime_arm_reports_layer_specific_error():
    stage3 = runtime_common.Stage3Runtime(
        sim=None, scene=None, arms={},
        build_error=runtime.Stage3RuntimeBuildError)
    with pytest.raises(runtime.Stage3RuntimeBuildError):
        stage3.arm("X")

    stage4 = runtime.Stage4Runtime(
        sim=None, scene=None, arms={},
        build_error=runtime.RuntimeBuildError)
    with pytest.raises(runtime.RuntimeBuildError):
        stage4.arm("X")


def test_runtime_dual_is_a_compatibility_shim():
    assert runtime_dual.build_stage3_runtime is runtime.build_stage3_runtime
    assert runtime_dual.RuntimeBuildError is runtime.Stage3RuntimeBuildError
    assert runtime.Stage3RuntimeBuildError.code == "STAGE3_RUNTIME"
    assert runtime.RuntimeBuildError.code == "STAGE4_RUNTIME"


def test_startup_summary_keeps_contract_order(monkeypatch):
    arm, semantics = _build(monkeypatch)

    assert list(arm.startup) == [
        "fk", "cup_radius_measured", "cup_radius_design", "other_unit",
        "self_pairs", "collections", "excluded", "workcell_aliases",
        "shadow_hidden", "station_aliases",
    ]
    assert arm.startup["other_unit"] == {"other_unit": "B"}
    assert arm.startup["collections"] == {"self": 3, "workcell": 3}
    assert arm.startup["station_aliases"] == ["Island_A_Station"]
    assert arm.cup_radius == pytest.approx(movecfg.CUP_RADIUS_DESIGN)
    assert semantics is None


def test_extra_startup_is_appended(monkeypatch):
    arm, semantics = _build(
        monkeypatch, probe_semantics=True,
        extra_startup={"conveyor_aliases": ["Conveyor_A"]})

    assert list(arm.startup)[-1] == "conveyor_aliases"
    assert arm.startup["conveyor_aliases"] == ["Conveyor_A"]
    assert semantics == {"ok": True}


def test_build_failure_destroys_partial_arm(monkeypatch):
    with pytest.raises(RuntimeError):
        _build(monkeypatch, fail_build=True)

    assert _Probe.instances and _Probe.instances[-1].destroyed is True


def test_semantics_failure_destroys_arm_and_probe(monkeypatch):
    with pytest.raises(RuntimeError) as excinfo:
        _build(monkeypatch, probe_semantics=True,
               semantics={"ok": False})

    assert "语义自检失败" in str(excinfo.value)
    assert _Collisions.instances[-1].destroyed is True
    assert _Probe.instances[-1].destroyed is True
