"""collisions.py — 对象角色、碰撞集合与 4.10 查询适配 (06-09 §3).

角色划分 (不是“把整类对象全局排除”):

    robot       影子 UR5 可见连杆 + 吸盘 Body (真实臂的运动学等价体)
    mount       安装平台 Island_A (只允许 link1 固定安装接触)
    workcell    conveyor pads / 工位台面 / 仓库 / 地面 / AGV 车体与载台
    parts       非目标动态物料
    target      当前目标物料 (只在允许接触的最终构型放行杯口接触)
    load        已吸附载荷的影子副本 (带载路径检查)

4.10 实测 (2026-10-21, docs/measurement/probe_stage2_inventory.py):
  - ``sim.checkCollision(e1, e2) -> (0|1, [obj1, obj2])``;
  - ``sim.checkDistance(e1, e2, threshold) -> (result, [7], [obj1, obj2])``,
    threshold 内无近邻时返回 ``(0, 全零, [-1,-1])``, 不能当作距离 0;
  - ``collidable`` bool 属性控制查询参与性; collection 以 option 0 创建时
    使用成员对象的自身属性;
  - ``objintparam_collection_self_collision_indicator`` 在 4.10 的
    ``checkCollision(collection, collection)`` 中**未观察到过滤效果**
    (1/1/2、1/2/3、1/10/100 均照样报碰撞), 因此自碰撞使用显式非相邻
    连杆对, 不依赖该编号.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field, replace
from enum import Enum
from typing import Iterable, Optional

from stage1 import config as base
from stage1.scene import alias_of, subtree

from . import config as cfg

WHAT_SINGLE = "single"
WHAT_TREE = "tree"
WHAT_ALL = "all"

COLL_SHADOW = "stage2_shadow"
COLL_SHADOW_LINKS = "stage2_shadow_links"
COLL_SHADOW_NO_BASE = "stage2_shadow_no_base"
COLL_WORKCELL = "stage2_workcell"
COLL_WORKCELL_MOUNT = "stage2_workcell_mount"
COLL_MOUNT = "stage2_mount"
COLL_PARTS = "stage2_parts"
COLL_TARGET = "stage2_target"
COLL_LOAD = "stage2_shadow_load"


class CollisionError(RuntimeError):
    """碰撞集合/查询配置错误"""


class QueryState(str, Enum):
    CLEAR = "CLEAR"
    COLLISION = "COLLISION"
    NEAR = "NEAR"
    UNKNOWN = "UNKNOWN"


@dataclass(frozen=True)
class QueryResult:
    name: str
    state: QueryState
    pair: tuple[int, int] = (-1, -1)
    pair_aliases: tuple[str, str] = ("", "")
    distance: Optional[float] = None
    threshold: Optional[float] = None
    message: str = ""

    @property
    def ok(self) -> bool:
        return self.state is QueryState.CLEAR

    @property
    def unknown(self) -> bool:
        return self.state is QueryState.UNKNOWN

    def as_dict(self) -> dict:
        return {
            "name": self.name,
            "state": self.state.value,
            "pair": list(self.pair),
            "pair_aliases": list(self.pair_aliases),
            "distance": None if self.distance is None
            else round(self.distance, 6),
            "threshold": self.threshold,
            "message": self.message,
        }


# ═══════════════════════════════════════════════════════════════
# 4.10 返回值解析
# ═══════════════════════════════════════════════════════════════

def parse_collision_result(name: str, ret) -> QueryResult:
    if ret is None:
        return QueryResult(name, QueryState.UNKNOWN, message="返回 None")
    if not isinstance(ret, (tuple, list)) or not ret:
        try:
            value = int(ret)
        except (TypeError, ValueError):
            return QueryResult(name, QueryState.UNKNOWN,
                               message=f"无法解析返回值: {ret!r}")
        return QueryResult(name, QueryState.COLLISION if value else
                           QueryState.CLEAR)
    try:
        result = int(ret[0])
    except (TypeError, ValueError):
        return QueryResult(name, QueryState.UNKNOWN,
                           message=f"result 非整数: {ret[0]!r}")
    pair = _parse_pair(ret[1] if len(ret) >= 2 else None)
    if result == 0:
        return QueryResult(name, QueryState.CLEAR, pair=pair)
    if result == 1:
        return QueryResult(name, QueryState.COLLISION, pair=pair)
    return QueryResult(name, QueryState.UNKNOWN,
                       pair=pair, message=f"未知 result={result}")


def parse_distance_result(name: str, ret,
                          threshold: float) -> QueryResult:
    """区分 CLEAR / NEAR / UNKNOWN.

    ``result=0`` 只表示“阈值内没有近邻”, 不是距离 0; 需要精确距离时必须
    用 threshold=0 另行查询。
    """
    if ret is None:
        return QueryResult(name, QueryState.UNKNOWN, threshold=threshold,
                           message="返回 None")
    if not isinstance(ret, (tuple, list)) or len(ret) < 3:
        return QueryResult(name, QueryState.UNKNOWN, threshold=threshold,
                           message=f"返回结构异常: {ret!r}")
    try:
        result = int(ret[0])
    except (TypeError, ValueError):
        return QueryResult(name, QueryState.UNKNOWN, threshold=threshold,
                           message=f"result 非整数: {ret[0]!r}")
    data = ret[1]
    pair = _parse_pair(ret[2])
    if result == 0:
        return QueryResult(name, QueryState.CLEAR, pair=pair,
                           threshold=threshold)
    if result != 1:
        return QueryResult(name, QueryState.UNKNOWN, pair=pair,
                           threshold=threshold, message=f"未知 result={result}")
    if not isinstance(data, (tuple, list)) or len(data) < 7:
        return QueryResult(name, QueryState.UNKNOWN, pair=pair,
                           threshold=threshold, message="distanceData 不完整")
    try:
        distance = float(data[6])
    except (TypeError, ValueError):
        return QueryResult(name, QueryState.UNKNOWN, pair=pair,
                           threshold=threshold, message="距离非数值")
    if not math.isfinite(distance):
        return QueryResult(name, QueryState.UNKNOWN, pair=pair,
                           threshold=threshold, message="距离非有限值")
    return QueryResult(name, QueryState.NEAR, pair=pair,
                       distance=distance, threshold=threshold)


def _parse_pair(value) -> tuple[int, int]:
    if isinstance(value, (tuple, list)) and len(value) >= 2:
        try:
            return int(value[0]), int(value[1])
        except (TypeError, ValueError):
            pass
    return (-1, -1)


def _what_constant(sim, what: str) -> int:
    if what == WHAT_TREE:
        return int(sim.handle_tree)
    if what == WHAT_ALL:
        return int(sim.handle_all)
    return int(sim.handle_single)


# ═══════════════════════════════════════════════════════════════
# 场景几何发现
# ═══════════════════════════════════════════════════════════════

@dataclass
class SceneGeometry:
    """按角色整理的句柄与 alias; 句柄只在当前会话有效."""

    links: list[int] = field(default_factory=list)
    link_aliases: list[str] = field(default_factory=list)
    pad_body: int = -1
    conveyor_pads: list[int] = field(default_factory=list)
    coarse_proxies: list[int] = field(default_factory=list)
    mount: int = -1
    workcell: list[int] = field(default_factory=list)
    workcell_aliases: list[str] = field(default_factory=list)
    excluded: list[dict] = field(default_factory=list)
    inventory: list[dict] = field(default_factory=list)


def _is_shape(sim, handle: int) -> bool:
    try:
        return int(sim.getObjectType(handle)) == int(sim.object_shape_type)
    except Exception:
        return False


def _shape_size(sim, handle: int) -> Optional[list[float]]:
    try:
        value = sim.getShapeBB(handle)
        size = (value[0] if isinstance(value, (tuple, list))
                and len(value) == 2 else value)
        return [float(size[0]), float(size[1]), float(size[2])]
    except Exception:
        return None


def discover_ur5_links(sim, arm_root: int) -> tuple[list[int], list[str]]:
    by_alias: dict[str, int] = {}
    for h in subtree(sim, arm_root):
        if not _is_shape(sim, h):
            continue
        alias = alias_of(sim, h)
        if alias.startswith("link") and alias.endswith("_visible"):
            by_alias[alias] = int(h)
    missing = [f"link{i}_visible" for i in range(1, cfg.UR5_LINK_COUNT + 1)
               if f"link{i}_visible" not in by_alias]
    if missing:
        raise CollisionError(f"UR5 可见连杆缺失: {missing}")
    aliases = [f"link{i}_visible" for i in range(1, cfg.UR5_LINK_COUNT + 1)]
    return [by_alias[a] for a in aliases], aliases


def discover_pad_body(sim, pad_root: int) -> int:
    for h in subtree(sim, pad_root):
        if alias_of(sim, h) == "Body" and _is_shape(sim, h):
            return int(h)
    raise CollisionError("吸盘子树找不到 Body shape")


def _model_bbox_size(sim, handle: int) -> Optional[list[float]]:
    values = []
    for name in ("min_x", "min_y", "min_z", "max_x", "max_y", "max_z"):
        param = getattr(sim, f"objfloatparam_modelbbox_{name}", None)
        if param is None:
            return None
        try:
            values.append(float(sim.getObjectFloatParam(handle, param)))
        except Exception:
            return None
    return [values[3] - values[0], values[4] - values[1],
            values[5] - values[2]]


def discover_conveyor_parts(sim, belt: int) -> tuple[list[int], list[int]]:
    """返回 (pads, 粗包围盒代理).

    内置 conveyor 模型根下的 ``shape`` 是覆盖整条带的粗包围盒 (实测
    0.2 x 0.2 x 1.2 m), 其顶面比真实带面高约 10 cm; 它不是承载几何。
    真实承载面由 50 个 ``pad`` 提供, 因此查询角色使用 pads, 粗代理记录为
    明确排除项 (06-09 §11.3–§11.4, 不是“收缩代理”).
    """
    pads: list[int] = []
    coarse: list[int] = []
    model_size = _model_bbox_size(sim, belt)
    for h in subtree(sim, belt):
        if not _is_shape(sim, h):
            continue
        alias = alias_of(sim, h)
        if alias == "pad":
            pads.append(int(h))
        elif alias == "shape":
            size = _shape_size(sim, h)
            # 模型根包围盒参数在 loadScene 后可能不可读; 粗代理的稳定特征
            # 是厚度远大于 pad (pad 0.005 m) 的整条带外包盒.
            coarse_by_size = (size is not None and size[2] > 0.02)
            coarse_by_bbox = (
                model_size is not None and size is not None
                and all(abs(size[i] - model_size[i]) <= 0.02
                        for i in range(3)))
            if coarse_by_size or coarse_by_bbox:
                coarse.append(int(h))
    if not pads:
        raise CollisionError("传送带子树找不到 pad 承载面")
    return pads, coarse


def discover_workcell(sim, scene, *, station_aliases=None,
                      conveyor_aliases=None,
                      extra_obstacles=()) -> list[int]:
    """工位/仓库/地面/AGV 车体与载台; 不含安装平台与 conveyor 粗代理.

    阶段三用 ``station_aliases`` 同时纳入 A/B 两个置物台, 用
    ``extra_obstacles`` 纳入另一台真实机械臂的连杆/吸盘与岛体. 传入的
    ``scene`` 可以是 `Stage3Scene` 或单工位视图, 只要实现 ``handle``.
    阶段四用 ``conveyor_aliases`` 把所有物流线的承载 pad 纳入工作区.
    """
    aliases = tuple(conveyor_aliases or (base.ALIASES["conveyor"],))
    handles: list[int] = []
    for alias in aliases:
        belt = scene.handle(alias)
        if belt < 0:
            continue
        pads, _ = discover_conveyor_parts(sim, belt)
        handles.extend(pads)
    stations = (station_aliases
                if station_aliases is not None
                else (base.ALIASES["station"],))
    for alias in (*stations, base.ALIASES["warehouse"], base.ALIASES["floor"]):
        h = scene.handle(alias)
        if h >= 0:
            handles.append(int(h))
    # AGV: 车体可见网格 + 载台 + 挡边; 车轮/脚轮与地面的滚动接触不作为障碍.
    agv = getattr(scene, "agv_root", -1)
    if agv >= 0:
        for h in subtree(sim, agv):
            if not _is_shape(sim, h):
                continue
            alias = alias_of(sim, h)
            if alias.startswith(("leftWheel", "rightWheel", "caster")):
                continue
            handles.append(int(h))
    for h in extra_obstacles or ():
        if h is not None and int(h) >= 0:
            handles.append(int(h))
    return _dedupe(handles)


def _dedupe(handles: Iterable[int]) -> list[int]:
    seen: set[int] = set()
    out: list[int] = []
    for h in handles:
        if h in seen:
            continue
        seen.add(h)
        out.append(h)
    return out


def describe_object(sim, handle: int) -> dict:
    """稳定对象描述 (alias/父级/类型/包围盒/查询属性), 供报告与契约使用."""
    try:
        parent = int(sim.getObjectParent(handle))
    except Exception:
        parent = -1
    props = {}
    for name, getter in (
            ("collidable", lambda: bool(sim.getBoolProperty(handle,
                                                            "collidable"))),
            ("measurable", lambda: bool(sim.getBoolProperty(handle,
                                                            "measurable"))),
            ("static", lambda: int(sim.getObjectInt32Param(
                handle, sim.shapeintparam_static))),
            ("respondable", lambda: int(sim.getObjectInt32Param(
                handle, sim.shapeintparam_respondable))),
    ):
        try:
            props[name] = getter()
        except Exception:
            props[name] = None
    return {
        "alias": alias_of(sim, handle),
        "handle": int(handle),
        "type": int(sim.getObjectType(handle)),
        "parent_alias": alias_of(sim, parent) if parent >= 0 else "",
        "bbox": _shape_size(sim, handle),
        "props": props,
    }


# ═══════════════════════════════════════════════════════════════
# 集合与运行期管理
# ═══════════════════════════════════════════════════════════════

@dataclass
class CollectionSpec:
    name: str
    roles: str
    items: list[dict] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {
            "name": self.name,
            "roles": self.roles,
            "items": [
                {"alias": item.get("alias", ""),
                 "role": item.get("role", ""),
                 "what": item.get("what", WHAT_SINGLE)}
                for item in self.items
            ],
        }


class CollisionRuntime:
    """阶段二碰撞集合与查询适配.

    只读查询; 唯一会修改场景属性的是**影子机器人自身**的 collidable/
    measurable (启动期一次性设置). 生产对象属性不修改, 粗代理只从集合
    角色中排除, 不改变其场景属性.
    """

    def __init__(self, sim, geometry: SceneGeometry):
        self.sim = sim
        self.geometry = geometry
        self.collections: dict[str, int] = {}
        self.specs: list[CollectionSpec] = []
        self.query_log: list[dict] = []
        self._self_pairs = self._build_self_pairs(geometry.links)
        self._target: int = -1
        self.base_check: Optional[QueryResult] = None

    # ── 构建 ────────────────────────────────────────────────

    @classmethod
    def build(cls, sim, scene, *, link_handles, link_aliases,
              pad_body: int, load_handle: int = -1,
              mount_alias: Optional[str] = None,
              station_aliases=None,
              conveyor_aliases=None,
              extra_obstacles=()) -> "CollisionRuntime":
        aliases = tuple(conveyor_aliases or (base.ALIASES["conveyor"],))
        pads: list[int] = []
        coarse: list[int] = []
        for alias in aliases:
            belt = scene.handle(alias)
            if belt < 0:
                continue
            belt_pads, belt_coarse = discover_conveyor_parts(sim, belt)
            pads.extend(belt_pads)
            coarse.extend(belt_coarse)
        mount_alias = mount_alias or base.ALIASES["island"]
        mount = scene.handle(mount_alias)
        if mount < 0:
            raise CollisionError(f"找不到安装平台 {mount_alias}")
        geometry = SceneGeometry(
            links=[int(h) for h in link_handles],
            link_aliases=list(link_aliases),
            pad_body=int(pad_body),
            conveyor_pads=[int(h) for h in pads],
            coarse_proxies=[int(h) for h in coarse],
            mount=int(mount),
            workcell=discover_workcell(
                sim, scene, station_aliases=station_aliases,
                conveyor_aliases=aliases,
                extra_obstacles=extra_obstacles),
        )
        geometry.workcell_aliases = [alias_of(sim, h)
                                     for h in geometry.workcell]
        geometry.excluded = [
            {"alias": alias_of(sim, h),
             "reason": "内置 conveyor 根下的整条带粗包围盒; "
                       "真实承载面由 pad 提供"}
            for h in coarse
        ]
        for role, handles in (("mount", [mount]),
                              ("workcell", geometry.workcell),
                              ("robot_link", link_handles),
                              ("pad_body", [pad_body])):
            for h in handles:
                geometry.inventory.append(
                    {**describe_object(sim, h), "role": role})
        runtime = cls(sim, geometry)
        runtime._create_collections(load_handle)
        # 载荷副本保持 collidable=False, 只在 set_attach_transform 时启用;
        # 未携带载荷时不能以任意相对位姿参与查询.
        runtime._ensure_collidable(link_handles, pad_body)
        runtime.base_check = runtime.base_vs_workcell()
        if runtime.base_check.state is not QueryState.CLEAR:
            runtime.destroy()
            raise CollisionError(
                f"UR5 基座与工作区初始检查失败: "
                f"{runtime.base_check.as_dict()}")
        return runtime

    def _create_collections(self, load_handle: int) -> None:
        g = self.geometry
        link_items = [{"alias": a, "handle": h, "role": "robot_link"}
                      for a, h in zip(g.link_aliases, g.links)]
        self._add_collection(
            COLL_SHADOW, "影子 UR5 全部可见连杆 + 吸盘 Body",
            link_items + [{"alias": "Body", "handle": g.pad_body,
                           "role": "pad_body"}])
        self._add_collection(COLL_SHADOW_LINKS, "影子 UR5 连杆 (不含吸盘)",
                             link_items)
        self._add_collection(
            COLL_SHADOW_NO_BASE, "除 link1 外的连杆 + 吸盘 Body (安装平台检查)",
            link_items[1:] + [{"alias": "Body", "handle": g.pad_body,
                               "role": "pad_body"}])
        self._add_collection(
            COLL_WORKCELL, "conveyor pads/工位台面/仓库/地面/AGV 车体与载台",
            [{"alias": alias_of(self.sim, h), "handle": h,
              "role": "workcell"} for h in g.workcell])
        self._add_collection(
            COLL_MOUNT, "UR5 安装平台 Island_A (仅 link1 固定安装接触)",
            [{"alias": alias_of(self.sim, g.mount), "handle": g.mount,
              "role": "mount"}])
        self._add_collection(
            COLL_WORKCELL_MOUNT,
            "工作区 + 安装平台; 用于除 link1 外的全部机器人 shape",
            [{"alias": alias_of(self.sim, h), "handle": h,
              "role": "workcell_mount"}
             for h in [*g.workcell, g.mount]])
        self._add_collection(COLL_PARTS, "非目标动态物料")
        self._add_collection(COLL_TARGET, "当前目标物料")
        self._add_collection(
            COLL_LOAD, "影子载荷 (仅带载检查)",
            ([{"alias": alias_of(self.sim, load_handle),
               "handle": load_handle, "role": "load"}] if load_handle >= 0
             else []))

    def _add_collection(self, name: str, roles: str,
                        items: Iterable[dict] = ()) -> int:
        handle = int(self.sim.createCollection(0))
        spec = CollectionSpec(name=name, roles=roles)
        for item in items:
            h = int(item["handle"])
            if h < 0:
                continue
            self.sim.addItemToCollection(
                handle, _what_constant(self.sim, WHAT_SINGLE), h, 0)
            spec.items.append({"alias": item.get("alias", ""),
                               "role": item.get("role", ""),
                               "what": WHAT_SINGLE})
        self.collections[name] = handle
        self.specs.append(spec)
        return handle

    def _ensure_collidable(self, *handle_groups) -> None:
        for group in handle_groups:
            handles = group if isinstance(group, (list, tuple)) else [group]
            for h in handles:
                if h is None or int(h) < 0:
                    continue
                try:
                    self.sim.setBoolProperty(int(h), "collidable", True)
                    self.sim.setBoolProperty(int(h), "measurable", True)
                except Exception as exc:      # 4.10 已知仅 bool property 路径
                    raise CollisionError(
                        f"无法设置 collidable/measurable: {exc}") from exc

    @staticmethod
    def _build_self_pairs(links: list[int]) -> list[tuple[int, int]]:
        pairs = []
        for i in range(len(links)):
            for j in range(i + cfg.UR5_SELF_PAIR_MIN_GAP, len(links)):
                pairs.append((links[i], links[j]))
        return pairs

    # ── 集合维护 ────────────────────────────────────────────

    def _item(self, collection: str, handle: int, remove: bool) -> None:
        if handle is None or int(handle) < 0:
            return
        try:
            self.sim.addItemToCollection(
                self.collections[collection],
                _what_constant(self.sim, WHAT_SINGLE), int(handle),
                1 if remove else 0)
        except Exception:
            # 对象可能已被删除 (任务间清理); 集合条目随句柄失效, 不阻断后续
            if not remove:
                raise

    def set_target(self, handle: Optional[int]) -> None:
        if self._target >= 0 and self._target != handle:
            self._item(COLL_TARGET, self._target, remove=True)
        self._target = int(handle) if handle is not None else -1
        if self._target >= 0:
            self._item(COLL_PARTS, self._target, remove=True)
            self._item(COLL_TARGET, self._target, remove=False)

    def set_target_carried(self, handle: Optional[int], carried: bool) -> None:
        """目标从“场景障碍”切换为“随工具运动”或反之."""
        self.set_target(handle)
        if carried and handle is not None:
            self._item(COLL_TARGET, handle, remove=True)
            self._item(COLL_PARTS, handle, remove=True)
        elif handle is not None:
            self._item(COLL_TARGET, handle, remove=False)
            self._item(COLL_PARTS, handle, remove=True)

    def add_part(self, handle: int) -> None:
        self._item(COLL_PARTS, int(handle), remove=False)

    def remove_part(self, handle: int) -> None:
        self._item(COLL_PARTS, int(handle), remove=True)
        if int(handle) == self._target:
            self._item(COLL_TARGET, int(handle), remove=True)

    def add_hazard(self, handle: int) -> None:
        # 查询使用 COLL_WORKCELL_MOUNT (工作区+安装平台); 必须同时加入两份
        self._item(COLL_WORKCELL, int(handle), remove=False)
        self._item(COLL_WORKCELL_MOUNT, int(handle), remove=False)

    def remove_hazard(self, handle: int) -> None:
        self._item(COLL_WORKCELL, int(handle), remove=True)
        self._item(COLL_WORKCELL_MOUNT, int(handle), remove=True)

    # ── 查询 ────────────────────────────────────────────────

    def _collision(self, name: str, e1: int, e2: int) -> QueryResult:
        try:
            result = parse_collision_result(
                name, self.sim.checkCollision(e1, e2))
        except Exception as exc:
            return QueryResult(name, QueryState.UNKNOWN,
                               message=f"checkCollision 异常: {exc}")
        return self._with_aliases(result)

    def _distance(self, name: str, e1: int, e2: int,
                  threshold: float) -> QueryResult:
        try:
            result = parse_distance_result(
                name, self.sim.checkDistance(e1, e2, float(threshold)),
                float(threshold))
        except Exception as exc:
            return QueryResult(name, QueryState.UNKNOWN,
                               threshold=float(threshold),
                               message=f"checkDistance 异常: {exc}")
        return self._with_aliases(result)

    def _with_aliases(self, result: QueryResult) -> QueryResult:
        if result.pair == (-1, -1):
            return result
        return replace(result, pair_aliases=(
            alias_of(self.sim, result.pair[0]),
            alias_of(self.sim, result.pair[1])))

    def robot_vs_workcell(self) -> QueryResult:
        """除 link1 外全部机器人 shape vs (工作区 + 安装平台).

        link1 是固定基座, 与安装平台的正常接触不能作为每步碰撞; 它由
        ``base_vs_workcell`` 在启动/目标构型单独检查.
        """
        return self._collision(
            "robot_vs_workcell", self.collections[COLL_SHADOW_NO_BASE],
            self.collections[COLL_WORKCELL_MOUNT])

    def base_vs_workcell(self) -> QueryResult:
        return self._collision("link1_vs_workcell", self.geometry.links[0],
                               self.collections[COLL_WORKCELL])

    def robot_vs_mount(self) -> QueryResult:
        """诊断用: 除 link1 外全部 shape vs 安装平台."""
        return self._collision("robot_vs_mount",
                               self.collections[COLL_SHADOW_NO_BASE],
                               self.collections[COLL_MOUNT])

    def robot_vs_parts(self) -> QueryResult:
        return self._collision("robot_vs_parts",
                               self.collections[COLL_SHADOW],
                               self.collections[COLL_PARTS])

    def robot_vs_target(self) -> QueryResult:
        return self._collision("robot_vs_target",
                               self.collections[COLL_SHADOW],
                               self.collections[COLL_TARGET])

    def links_vs_target(self) -> QueryResult:
        return self._collision("links_vs_target",
                               self.collections[COLL_SHADOW_LINKS],
                               self.collections[COLL_TARGET])

    def pad_vs_target(self) -> QueryResult:
        return self._collision("pad_vs_target", self.geometry.pad_body,
                               self.collections[COLL_TARGET])

    def load_vs_workcell(self) -> QueryResult:
        return self._collision("load_vs_workcell",
                               self.collections[COLL_LOAD],
                               self.collections[COLL_WORKCELL_MOUNT])

    def load_distance_margin(self, threshold: float = 0.0) -> QueryResult:
        """载荷到工作区/承载面的精确距离 (诊断净空用, 不做放行判据)."""
        return self._distance("load_distance_margin",
                              self.collections[COLL_LOAD],
                              self.collections[COLL_WORKCELL_MOUNT],
                              threshold)

    def load_vs_parts(self) -> QueryResult:
        return self._collision("load_vs_parts",
                               self.collections[COLL_LOAD],
                               self.collections[COLL_PARTS])

    def load_vs_world(self) -> QueryResult:
        first = self.load_vs_workcell()
        if first.state is not QueryState.CLEAR:
            return first
        return self.load_vs_parts()

    def self_collision(self) -> QueryResult:
        for a, b in self._self_pairs:
            result = self._collision("self_link_pair", a, b)
            if result.state is not QueryState.CLEAR:
                return result
        pad = self.geometry.pad_body
        for index, link in enumerate(self.geometry.links, start=1):
            if index in cfg.PAD_ALLOWED_LINK_INDICES:
                continue
            result = self._collision("self_pad_link_pair", pad, link)
            if result.state is not QueryState.CLEAR:
                return result
        return QueryResult("self_collision", QueryState.CLEAR)

    def distance_margin(self, threshold: float) -> QueryResult:
        return self._distance("robot_distance_margin",
                              self.collections[COLL_SHADOW_NO_BASE],
                              self.collections[COLL_WORKCELL_MOUNT],
                              threshold)

    def probe_configuration(self, *, check_target: bool,
                            allow_target_contact: bool,
                            check_load: bool,
                            margin: Optional[float] = None,
                            check_self: bool = True) -> list[QueryResult]:
        """一个构型的完整查询集合; 返回全部结果, 由调用方判定.

        ``allow_target_contact`` 只表示“杯口与目标表面可以接触”, 是否真的
        允许由调用方的接触几何判定; 这里始终返回 pad/links 与目标的查询
        结果, 便于记录对象对.
        """
        results: list[QueryResult] = []
        if check_self:
            results.append(self.self_collision())
        results.extend([self.robot_vs_workcell(), self.robot_vs_parts()])
        if check_target and self._target >= 0:
            results.append(self.links_vs_target())
            results.append(self.pad_vs_target())
        if check_load:
            results.append(self.load_vs_workcell())
            results.append(self.load_vs_parts())
        if margin is not None and margin > 0:
            results.append(self.distance_margin(margin))
        return results

    # ── 自检 / 报告 ─────────────────────────────────────────

    def query_semantics_probe(self) -> dict:
        """用临时对象实测查询语义 (06-09 §3.4), 场景必须处于停止态."""
        sim = self.sim
        out: dict = {}
        handles: list[int] = []
        try:
            for _ in range(3):
                h = int(sim.createPrimitiveShape(
                    sim.primitiveshape_cuboid, [0.1, 0.1, 0.1]))
                handles.append(h)
            sim.setObjectPosition(handles[0], [3.5, -2.5, 0.05],
                                  sim.handle_world)
            sim.setObjectPosition(handles[1], [3.55, -2.5, 0.05],
                                  sim.handle_world)     # 与 0 相交
            sim.setObjectPosition(handles[2], [3.9, -2.5, 0.05],
                                  sim.handle_world)     # 与 0 相距 0.4
            overlap = parse_collision_result(
                "probe_overlap", sim.checkCollision(handles[0], handles[1]))
            separated = parse_collision_result(
                "probe_separated", sim.checkCollision(handles[0], handles[2]))
            near = parse_distance_result(
                "probe_distance_threshold",
                sim.checkDistance(handles[0], handles[2], 0.5), 0.5)
            far = parse_distance_result(
                "probe_distance_outside",
                sim.checkDistance(handles[0], handles[2], 0.01), 0.01)
            exact = parse_distance_result(
                "probe_distance_exact",
                sim.checkDistance(handles[0], handles[2], 0.0), 0.0)
            sim.setBoolProperty(handles[2], "collidable", False)
            disabled = parse_collision_result(
                "probe_collidable_false",
                sim.checkCollision(handles[0], handles[2]))
            out = {
                "overlap": overlap.as_dict(),
                "separated": separated.as_dict(),
                "distance_with_threshold": near.as_dict(),
                "distance_outside_threshold": far.as_dict(),
                "distance_exact": exact.as_dict(),
                "collidable_false": disabled.as_dict(),
                "ok": (overlap.state is QueryState.COLLISION
                       and separated.state is QueryState.CLEAR
                       and near.state is QueryState.NEAR
                       and far.state is QueryState.CLEAR
                       and disabled.state is QueryState.CLEAR),
            }
        except Exception as exc:
            out = {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
        finally:
            for h in handles:
                try:
                    sim.removeObjects([h])
                except Exception:
                    pass
        return out

    def spec_dict(self) -> list[dict]:
        return [spec.as_dict() for spec in self.specs]

    def destroy(self) -> None:
        for name, handle in list(self.collections.items()):
            try:
                self.sim.destroyCollection(handle)
            except Exception:
                pass
        self.collections.clear()
        self.specs.clear()
