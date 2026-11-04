"""operation_runtime.py — v1 UFLOW 工序实例、配方运行图与 ready 集合 (06-21).

三层职责, 全部是纯 Python, 不导入 CoppeliaSim:

  - :class:`OperationInstance` 是一次运行中**单道工序**的唯一状态记录. 阶段 0-1
    只保存生命周期字段; 阶段 3 起增加 run/recipe/route/依赖/输入输出上下文,
    并继续作为 :class:`~flexible_factory.domain.tasks.Stage4Task` 的底层对象
    (任务上的同名字段是受控代理, 不存在第二份状态).
  - :class:`RecipeRunContext` 是一次有限批次的运行上下文 (run_id、recipe 版本、
    入口物料、选定 route variant). v1 不引入可持久化订单/MES 实体.
  - :class:`OperationGraph` 按 ``instance_id`` 索引本次运行的实例, 提供前驱/
    后继/模板/UID 索引、图级校验与 ready 集合;
    :class:`OperationRuntime` 是协调器唯一可用的窄接口 (实例化 / mark_started /
    commit_success / mark_failed).

不变量:
  - 后继只在本实例的**实际**前驱全部 DONE 且输出已提交后 ready;
  - 共享尾段 (C/E) 每个 UID 只实例化一次, 前驱绑定到该 UID 实际选中的路线,
    不把未选路线写成 AND 前驱;
  - 依赖未满足的实例留在图中, 不提前进入任务队列;
  - 重复提交、提交未解锁实例、提交已 FAILED 实例都抛
    :class:`OperationRuntimeError`; 失败前驱不解锁后继, 不重试也不改线.
  - 阶段 4 起, 调度器选定的设备记录在 ``selected_device_id``; 执行启动后
    不得改派. 等待/阻塞/恢复/阶段/资源/升级都经本模块的窄方法写入,
    recipe-backed 任务不能再通过旧 setter 直接改状态.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Iterable, Optional

from .process_model import (MaterialPort, OperationTemplate, ProcessCatalog,
                            ProcessModelError, RecipeSpec, RouteVariant)


class TaskStatus(str, Enum):
    PENDING = "PENDING"
    WAITING = "WAITING"
    RUNNING = "RUNNING"
    BLOCKED = "BLOCKED"
    DONE = "DONE"
    FAILED = "FAILED"
    REJECTED = "REJECTED"

    @property
    def terminal(self) -> bool:
        return self in (TaskStatus.DONE, TaskStatus.FAILED,
                        TaskStatus.REJECTED)

    @property
    def active(self) -> bool:
        return self in (TaskStatus.PENDING, TaskStatus.WAITING,
                        TaskStatus.RUNNING, TaskStatus.BLOCKED)


class OperationRuntimeError(RuntimeError):
    code = "V1_OPERATION_RUNTIME"


# ═══════════════════════════════════════════════════════════════
# 运行上下文与工序实例
# ═══════════════════════════════════════════════════════════════

@dataclass(frozen=True)
class RecipeRunContext:
    """一次有限批次的运行上下文; 只描述本批次的入口选择, 不是订单实体."""

    run_id: str
    recipe_id: str
    recipe_version: str
    entry_id: str
    entry_uid: int
    entry_item_type: str
    entry_zone: str
    route_variant_id: str
    created_sim_time: float = 0.0

    def as_dict(self) -> dict:
        return {
            "run_id": self.run_id,
            "recipe_id": self.recipe_id,
            "recipe_version": self.recipe_version,
            "entry_id": self.entry_id,
            "entry_uid": int(self.entry_uid),
            "entry_item_type": self.entry_item_type,
            "entry_zone": self.entry_zone,
            "route_variant_id": self.route_variant_id,
            "created_sim_time": round(float(self.created_sim_time), 4),
        }


@dataclass
class OperationInstance:
    """一道已实例化工序的运行时记录.

    稳定字段 (`instance_id`/`template_id`/`kind`/`uid`/`source`/
    `destination`/`dock`/`device`) 创建后只读; 生命周期和时间字段由
    :class:`~flexible_factory.domain.tasks.Stage4Task` 的受控属性或
    :class:`OperationRuntime` 的窄接口写入.

    ``instance_id`` 是确定性组合 ``run_id:uid:operation_id``, 不依赖模板元组
    排列; ``task_id`` 是任务队列/证据使用的兼容键 (``kind_uid{n}``), 两者
    一一对应但不互相推导.
    """

    instance_id: str
    template_id: str
    kind: str
    uid: int
    source: str
    destination: str
    dock: str = ""
    device: str = ""
    transport: bool = False
    # ── 阶段 3: 运行上下文与依赖 ────────────────────────────
    task_id: str = ""
    run_id: str = ""
    recipe_id: str = ""
    recipe_version: str = ""
    entry_id: str = ""
    selected_route: str = ""
    predecessor_instance_ids: list[str] = field(default_factory=list)
    input_uids: list[int] = field(default_factory=list)
    output_uids: list[int] = field(default_factory=list)
    capability: str = ""
    candidate_device_ids: list[str] = field(default_factory=list)
    # 阶段 4: 调度器选定的设备; 必须属于 candidate_device_ids, 启动后不可更换.
    selected_device_id: str = ""
    current_work_step: str = ""
    sequence: int = 0
    dispatched: bool = False
    committed_sim_time: Optional[float] = None
    # ── 生命周期 ────────────────────────────────────────────
    state: TaskStatus = TaskStatus.PENDING
    phase: str = ""
    priority: int = 1
    attempts: int = 0
    # 进入可派工队列的时间 (用于优先级老化); 后继实例在 claim_ready 时刷新,
    # 批次创建时间单独记录在 RecipeRunContext.created_sim_time.
    created_sim_time: float = 0.0
    started_sim_time: Optional[float] = None
    finished_sim_time: Optional[float] = None
    waiting_since: Optional[float] = None
    blocked_since: Optional[float] = None
    failure_reason: str = ""
    evidence: dict = field(default_factory=dict)
    resources: list[str] = field(default_factory=list)
    upgraded: bool = False

    @property
    def committed(self) -> bool:
        return self.committed_sim_time is not None

    def as_dict(self) -> dict:
        """序列化; 阶段 0-1 的键名保持不变, 阶段 3 字段只做追加."""
        return {
            "instance_id": self.instance_id,
            "task_id": self.task_id,
            "template_id": self.template_id,
            "kind": self.kind,
            "uid": int(self.uid),
            "source": self.source,
            "destination": self.destination,
            "dock": self.dock,
            "device": self.device,
            "state": self.state.value,
            "phase": self.phase,
            "priority": int(self.priority),
            "attempts": int(self.attempts),
            "created_sim_time": round(float(self.created_sim_time), 4),
            "started_sim_time": (None if self.started_sim_time is None
                                 else round(float(self.started_sim_time), 4)),
            "finished_sim_time": (None if self.finished_sim_time is None
                                  else round(float(self.finished_sim_time), 4)),
            "failure_reason": self.failure_reason,
            "resources": list(self.resources),
            "transport": bool(self.transport),
            "run_id": self.run_id,
            "recipe_id": self.recipe_id,
            "recipe_version": self.recipe_version,
            "entry_id": self.entry_id,
            "selected_route": self.selected_route,
            "predecessor_instance_ids": list(self.predecessor_instance_ids),
            "input_uids": [int(uid) for uid in self.input_uids],
            "output_uids": [int(uid) for uid in self.output_uids],
            "capability": self.capability,
            "candidate_device_ids": list(self.candidate_device_ids),
            "selected_device_id": self.selected_device_id,
            "current_work_step": self.current_work_step,
            "sequence": int(self.sequence),
            "dispatched": bool(self.dispatched),
            "committed_sim_time": (None if self.committed_sim_time is None
                                   else round(float(self.committed_sim_time),
                                              4)),
        }


# ═══════════════════════════════════════════════════════════════
# 模板 → 旧任务字段的派生 (唯一实现)
# ═══════════════════════════════════════════════════════════════

def legacy_device_id(template: OperationTemplate) -> str:
    """工序模板 → 旧 ``Stage4Task.device`` 兼容标识."""
    device_id = template.candidate_devices[0]
    if device_id.startswith("arm_"):
        return device_id.removeprefix("arm_")
    if template.executor_kind == "agv":
        return "agv"
    return device_id


def legacy_task_fields(template: OperationTemplate) -> dict:
    """工序模板 → 旧任务稳定字段; 阶段 2 之前散落在 ``tasks.py``."""
    if template.executor_kind == "agv":
        target = template.route_destination_dock
        return {
            "source": f"dock_{template.route_origin_dock}",
            "destination": f"dock_{target}",
            "dock": target,
            "device": "agv",
            "transport": True,
        }
    return {
        "source": template.inputs[0].zone if template.inputs else "",
        "destination": template.output.zone if template.output else "",
        "dock": template.dock,
        "device": legacy_device_id(template),
        "transport": False,
    }


def _port_matches(output: Optional[MaterialPort], port: MaterialPort) -> bool:
    return (output is not None and output.zone == port.zone
            and output.item_type == port.item_type)


# ═══════════════════════════════════════════════════════════════
# 实例图
# ═══════════════════════════════════════════════════════════════

class OperationGraph:
    """一次运行的实例图; 只做索引、ready 判定和结构校验, 不推进执行."""

    def __init__(self, run: Optional[RecipeRunContext] = None) -> None:
        self.run = run
        self._instances: dict[str, OperationInstance] = {}
        self._order: list[str] = []
        self._predecessors: dict[str, tuple[str, ...]] = {}
        self._successors: dict[str, list[str]] = {}
        self._by_task_id: dict[str, str] = {}
        self._sequence = 0

    # ── 构建 ────────────────────────────────────────────────

    def add(self, instance: OperationInstance) -> OperationInstance:
        key = instance.instance_id
        if key in self._instances:
            raise OperationRuntimeError(f"重复实例 ID: {key!r}")
        if instance.task_id and instance.task_id in self._by_task_id:
            raise OperationRuntimeError(
                f"重复任务键 {instance.task_id!r} (实例 {key!r})")
        if not instance.sequence:
            self._sequence += 1
            instance.sequence = self._sequence
        else:
            self._sequence = max(self._sequence, int(instance.sequence))
        self._instances[key] = instance
        self._order.append(key)
        self._predecessors[key] = tuple(instance.predecessor_instance_ids)
        for predecessor in instance.predecessor_instance_ids:
            if predecessor in self._instances:
                self._successors.setdefault(predecessor, []).append(key)
        if instance.task_id:
            self._by_task_id[instance.task_id] = key
        return instance

    def link(self, predecessor_id: str, successor_id: str) -> None:
        """登记一条前驱→后继边; 两个端点都必须已在图内.

        正常实例化只走 :meth:`add`; 该接口用于测试与结构诊断, 也是
        cycle/闭合性校验的入口.
        """
        if predecessor_id not in self._instances:
            raise OperationRuntimeError(f"未知前驱实例: {predecessor_id!r}")
        if successor_id not in self._instances:
            raise OperationRuntimeError(f"未知后继实例: {successor_id!r}")
        successors = self._successors.setdefault(predecessor_id, [])
        if successor_id not in successors:
            successors.append(successor_id)
        predecessors = list(self._predecessors.get(successor_id, ()))
        if predecessor_id not in predecessors:
            predecessors.append(predecessor_id)
        self._predecessors[successor_id] = tuple(predecessors)
        self._instances[successor_id].predecessor_instance_ids = \
            list(predecessors)

    # ── 查询 ────────────────────────────────────────────────

    def get(self, instance_id: str) -> Optional[OperationInstance]:
        return self._instances.get(instance_id)

    def require(self, instance_id: str) -> OperationInstance:
        instance = self._instances.get(instance_id)
        if instance is None:
            raise OperationRuntimeError(f"未知工序实例: {instance_id!r}")
        return instance

    def by_task_id(self, task_id: str) -> Optional[OperationInstance]:
        key = self._by_task_id.get(str(task_id))
        return self._instances.get(key) if key is not None else None

    def instances(self) -> list[OperationInstance]:
        return [self._instances[key] for key in self._order]

    def by_template(self, template_id: str) -> list[OperationInstance]:
        return [row for row in self.instances()
                if row.template_id == template_id]

    def by_uid(self, uid: int) -> list[OperationInstance]:
        return [row for row in self.instances() if int(row.uid) == int(uid)]

    def predecessors(self, instance_id: str) -> tuple[str, ...]:
        return self._predecessors.get(str(instance_id), ())

    def successors(self, instance_id: str) -> tuple[str, ...]:
        return tuple(self._successors.get(str(instance_id), ()))

    def pending_join_nodes(self, template_id: str) -> list[OperationInstance]:
        return [row for row in self.by_template(template_id)
                if row.state.active]

    def __len__(self) -> int:
        return len(self._instances)

    def __contains__(self, instance_id: object) -> bool:
        return instance_id in self._instances

    # ── ready 集合 ──────────────────────────────────────────

    def unlocked(self, instance: OperationInstance) -> bool:
        """全部实际前驱 DONE 且输出已提交; 无前驱的入口节点恒为真."""
        for predecessor_id in self._predecessors.get(instance.instance_id, ()):
            predecessor = self._instances.get(predecessor_id)
            if predecessor is None:
                return False
            if predecessor.state is not TaskStatus.DONE:
                return False
            if predecessor.committed_sim_time is None:
                return False
        return True

    def ready(self) -> list[OperationInstance]:
        """当前可派工的实例; 按 (priority, 创建序号, 实例 ID) 稳定排序."""
        rows = [row for row in self.instances()
                if row.state is TaskStatus.PENDING and not row.dispatched
                and self.unlocked(row)]
        rows.sort(key=lambda row: (int(row.priority), int(row.sequence),
                                   row.instance_id))
        return rows

    def claim_ready(self, sim_time: Optional[float] = None
                    ) -> list[OperationInstance]:
        """取出 ready 实例并标记已派发; 同一实例不会被重复提交.

        给定 ``sim_time`` 时把实例的 ``created_sim_time`` 改写为释放时刻:
        该字段用于任务优先级老化, 语义是"进入可派工队列的时间", 与
        :class:`RecipeRunContext` 的批次创建时间不同.
        """
        rows = self.ready()
        for row in rows:
            row.dispatched = True
            if sim_time is not None:
                row.created_sim_time = float(sim_time)
        return rows

    def all_done(self) -> bool:
        return bool(self._instances) and all(
            row.state is TaskStatus.DONE for row in self.instances())

    # ── 校验 ────────────────────────────────────────────────

    def validate(self, catalog: ProcessCatalog,
                 recipe: RecipeSpec) -> list[str]:
        """图级校验; 返回带 run/recipe/operation/instance 定位的诊断."""
        issues: list[str] = []
        run_prefix = (f"run={self.run.run_id} " if self.run is not None else
                      "")
        recipe_prefix = f"recipe={recipe.id}"

        seen_task_ids: dict[str, str] = {}
        for instance in self.instances():
            prefix = (f"{run_prefix}{recipe_prefix} "
                      f"operation={instance.template_id} "
                      f"instance={instance.instance_id}: ")
            if instance.task_id:
                owner = seen_task_ids.setdefault(instance.task_id,
                                                 instance.instance_id)
                if owner != instance.instance_id:
                    issues.append(prefix + f"任务键 {instance.task_id!r} "
                                           f"与实例 {owner!r} 冲突")
            try:
                template = catalog.operation(instance.template_id,
                                             recipe.id)
            except ProcessModelError as exc:
                issues.append(prefix + str(exc))
                continue
            if instance.kind != template.kind:
                issues.append(prefix + f"kind={instance.kind!r} 与模板 "
                                       f"{template.kind!r} 不一致")
            if template.executor_kind != "logic" and not instance.selected_route:
                issues.append(prefix + "缺少 selected_route")
            issues.extend(self._check_predecessors(instance, template,
                                                   catalog, recipe, prefix))
            if not instance.selected_route:
                continue
            route = _find_route(recipe, instance.selected_route)
            if route is None:
                issues.append(prefix + f"未知 route variant "
                                       f"{instance.selected_route!r}")
            elif instance.template_id not in route.operation_ids:
                issues.append(prefix + f"工序 {instance.template_id!r} 不属于 "
                                       f"所选路线 {route.id!r}")

        issues.extend(self._check_cycles(run_prefix, recipe_prefix))
        issues.extend(self._check_routes(recipe, run_prefix, recipe_prefix))
        return issues

    def _check_predecessors(self, instance: OperationInstance,
                            template: OperationTemplate,
                            catalog: ProcessCatalog, recipe: RecipeSpec,
                            prefix: str) -> list[str]:
        issues: list[str] = []
        matched_ports: set[int] = set()
        for predecessor_id in self._predecessors.get(instance.instance_id, ()):
            predecessor = self._instances.get(predecessor_id)
            if predecessor is None:
                issues.append(prefix + f"前驱实例不在图内: {predecessor_id!r}")
                continue
            try:
                predecessor_template = catalog.operation(
                    predecessor.template_id, recipe.id)
            except ProcessModelError:
                continue
            output = predecessor_template.output
            matched = [index for index, port in enumerate(template.inputs)
                       if _port_matches(output, port)]
            if not matched:
                output_desc = ("" if output is None else
                               f"{output.zone}/{output.item_type.value}")
                issues.append(
                    prefix + f"前驱 {predecessor.template_id!r} 的输出 "
                             f"{output_desc} 不能接入本工序输入")
                continue
            if template.executor_kind == "logic":
                free = [index for index in matched
                        if index not in matched_ports]
                if not free:
                    issues.append(
                        prefix + f"前驱 {predecessor.template_id!r} 与其他"
                                 f"前驱占用同一输入端口")
                    continue
                matched_ports.add(free[0])
        if template.executor_kind == "logic" \
                and len(matched_ports) != len(template.inputs):
            issues.append(
                prefix + f"合流端口未闭合: 已绑定 {sorted(matched_ports)}, "
                         f"模板输入 {len(template.inputs)} 个")
        return issues

    def _check_cycles(self, run_prefix: str, recipe_prefix: str) -> list[str]:
        state: dict[str, int] = {}
        cycles: list[list[str]] = []

        def visit(node: str, stack: list[str]) -> None:
            mark = state.get(node, 0)
            if mark == 2:
                return
            if mark == 1:
                start = stack.index(node) if node in stack else 0
                cycles.append(stack[start:] + [node])
                return
            state[node] = 1
            stack.append(node)
            for successor in self._successors.get(node, ()):
                if successor in self._instances:
                    visit(successor, stack)
            stack.pop()
            state[node] = 2

        for key in self._order:
            visit(key, [])
        return [f"{run_prefix}{recipe_prefix} instance={path[0]}: "
                f"循环依赖 " + " -> ".join(path) for path in cycles]

    def _check_routes(self, recipe: RecipeSpec, run_prefix: str,
                      recipe_prefix: str) -> list[str]:
        issues: list[str] = []
        by_run: dict[str, list[OperationInstance]] = {}
        for instance in self.instances():
            if not instance.run_id or not instance.selected_route:
                continue
            by_run.setdefault(instance.run_id, []).append(instance)
        for run_id, rows in by_run.items():
            route_id = rows[0].selected_route
            route = _find_route(recipe, route_id)
            if route is None:
                continue
            actual = [row.template_id for row in rows]
            expected = list(route.operation_ids)
            duplicated = sorted({op_id for op_id in actual
                                 if actual.count(op_id) > 1})
            if duplicated:
                issues.append(
                    f"{run_prefix}{recipe_prefix} route={route_id}: "
                    f"同一 UID 重复实例化 {duplicated}")
            missing = [op_id for op_id in expected if op_id not in actual]
            if missing:
                issues.append(
                    f"{run_prefix}{recipe_prefix} route={route_id}: "
                    f"缺少路线工序 {missing}")
            extra = [op_id for op_id in actual if op_id not in expected]
            if extra:
                issues.append(
                    f"{run_prefix}{recipe_prefix} route={route_id}: "
                    f"出现未选路线工序 {extra}")
            terminal = route.terminal_operation_id
            terminal_rows = [row for row in rows
                             if row.template_id == terminal]
            if not terminal_rows:
                issues.append(
                    f"{run_prefix}{recipe_prefix} route={route_id} "
                    f"instance={run_id}: 选中路线缺少终点 {terminal!r}")
                continue
            # 终点必须是该 UID 路线的最后一道工序; 跨 UID 的合流节点不属于
            # 该路线 (selected_route 为空), 不会被算作同路线后继.
            terminal_ids = {row.instance_id for row in terminal_rows}
            followers = sorted({
                row.template_id for row in rows
                if terminal_ids & set(row.predecessor_instance_ids)})
            if followers:
                issues.append(
                    f"{run_prefix}{recipe_prefix} route={route_id} "
                    f"instance={run_id}: 路线终点 {terminal!r} 之后仍有同路线"
                    f"工序 {followers}")
        return issues

    # ── 报告 ────────────────────────────────────────────────

    def as_dict(self) -> dict:
        return {
            "run": None if self.run is None else self.run.as_dict(),
            "instance_count": len(self._instances),
            "instances": [row.as_dict() for row in self.instances()],
            "ready": [row.instance_id for row in self.ready()],
            "all_done": self.all_done(),
        }


def _find_route(recipe: RecipeSpec,
                route_id: str) -> Optional[RouteVariant]:
    for route in recipe.route_variants:
        if route.id == route_id:
            return route
    return None


# ═══════════════════════════════════════════════════════════════
# 运行时窄接口
# ═══════════════════════════════════════════════════════════════

class OperationRuntime:
    """协调器唯一可用的工序运行接口: 实例化、启动、提交、失败.

    只持有 :class:`OperationGraph` 与已校验的静态目录, 不做资源申请、不做
    设备派工、不写物料账本; 这些仍由 coordinator 与
    :class:`~flexible_factory.domain.materials.MaterialRegistry` 负责.
    """

    def __init__(self, catalog: ProcessCatalog, recipe: RecipeSpec, *,
                 run_prefix: str = "run") -> None:
        self.catalog = catalog
        self.recipe = recipe
        self.graph = OperationGraph()
        self._run_prefix = str(run_prefix)
        self._run_serial = 0
        self._runs: dict[str, RecipeRunContext] = {}

    # ── 实例化 ──────────────────────────────────────────────

    def next_run_id(self) -> str:
        self._run_serial += 1
        return f"{self._run_prefix}{self._run_serial:04d}"

    def route_for_entry(self, entry_id: str) -> RouteVariant:
        entry = self._entry(entry_id)
        route_id = entry.route_variant_id
        if not route_id:
            raise OperationRuntimeError(
                f"recipe={self.recipe.id} entry={entry_id!r}: "
                f"缺少 route_variant_id")
        route = _find_route(self.recipe, route_id)
        if route is None:
            raise OperationRuntimeError(
                f"recipe={self.recipe.id} entry={entry_id!r}: "
                f"未知 route variant {route_id!r}")
        return route

    def instantiate_entry(self, entry_id: str, uid: int, *,
                          item_type: str = "",
                          sim_time: float = 0.0) -> list[OperationInstance]:
        """按入口选定的 route variant 实例化该 UID 的工序链并加入图."""
        entry = self._entry(entry_id)
        route = self.route_for_entry(entry_id)
        first = route.operation_ids[0] if route.operation_ids else ""
        if first != entry.operation_id:
            raise OperationRuntimeError(
                f"recipe={self.recipe.id} entry={entry_id!r} "
                f"route={route.id!r}: 入口工序 {entry.operation_id!r} 不是"
                f"路线首工序 {first!r}")
        run = RecipeRunContext(
            run_id=self.next_run_id(), recipe_id=self.recipe.id,
            recipe_version=self.recipe.version, entry_id=entry_id,
            entry_uid=int(uid),
            entry_item_type=item_type or entry.item_type.value,
            entry_zone=entry.line, route_variant_id=route.id,
            created_sim_time=float(sim_time))
        instances = self._build_chain(route, run, int(uid))
        # 先整体核对唯一性, 避免部分加入后留下半张图.
        for instance in instances:
            if instance.instance_id in self.graph:
                raise OperationRuntimeError(
                    f"重复实例 ID: {instance.instance_id!r}")
            if self.graph.by_task_id(instance.task_id) is not None:
                raise OperationRuntimeError(
                    f"recipe={self.recipe.id} uid={uid}: 任务键 "
                    f"{instance.task_id!r} 已被 "
                    f"{self.graph.by_task_id(instance.task_id).instance_id!r} "
                    f"占用")
        for instance in instances:
            self.graph.add(instance)
        self._runs[run.run_id] = run
        return instances

    def _build_chain(self, route: RouteVariant, run: RecipeRunContext,
                     uid: int) -> list[OperationInstance]:
        instances: list[OperationInstance] = []
        previous: Optional[OperationInstance] = None
        for operation_id in route.operation_ids:
            template = self.catalog.operation(operation_id, self.recipe.id)
            instance = self._build_instance(
                template, run=run, uid=uid,
                predecessors=() if previous is None else
                (previous.instance_id,),
                input_uids=() if previous is None else (uid,),
                route_id=route.id)
            if previous is not None:
                self._require_connectivity(previous, instance, run, route)
            instances.append(instance)
            previous = instance
        return instances

    def _require_connectivity(self, previous: OperationInstance,
                              instance: OperationInstance,
                              run: RecipeRunContext,
                              route: RouteVariant) -> None:
        output = self.catalog.operation(previous.template_id,
                                        self.recipe.id).output
        template = self.catalog.operation(instance.template_id,
                                          self.recipe.id)
        if not any(_port_matches(output, port) for port in template.inputs):
            raise OperationRuntimeError(
                f"run={run.run_id} recipe={self.recipe.id} route={route.id} "
                f"operation={instance.template_id} "
                f"instance={instance.instance_id}: 选路线不连续 "
                f"{previous.template_id} -> {instance.template_id} "
                f"(输出/输入区域或类型不匹配)")

    def add_join(self, *, parents: Iterable[OperationInstance],
                 input_uids: Iterable[int], uid: int,
                 sim_time: float = 0.0) -> OperationInstance:
        """在两条输入工序均完成后创建绑定父实例的合流节点.

        调用方负责在账本提交失败时 ``mark_failed``; 本方法只校验端口绑定,
        不选配对、不提交物料.
        """
        join_id = self.recipe.join_operation_id
        if not join_id:
            raise OperationRuntimeError(
                f"recipe={self.recipe.id}: 未声明 join_operation_id")
        parent_rows = list(parents)
        if len(parent_rows) != 2:
            raise OperationRuntimeError(
                f"recipe={self.recipe.id} operation={join_id}: "
                f"合流需要两个父实例, 实际 {len(parent_rows)}")
        template = self.catalog.operation(join_id, self.recipe.id)
        matched: set[int] = set()
        for parent in parent_rows:
            row = self.graph.get(parent.instance_id)
            if row is None:
                raise OperationRuntimeError(
                    f"instance={parent.instance_id}: 父实例不在图内")
            if row.state is not TaskStatus.DONE or not row.committed:
                raise OperationRuntimeError(
                    f"instance={row.instance_id}: 父实例未提交, 不能创建合流")
            output = self.catalog.operation(row.template_id,
                                            self.recipe.id).output
            candidates = [index for index, port in enumerate(template.inputs)
                          if _port_matches(output, port)
                          and index not in matched]
            if not candidates:
                raise OperationRuntimeError(
                    f"instance={row.instance_id}: 输出不能绑定合流输入 "
                    f"{join_id}")
            matched.add(candidates[0])
        if len(matched) != len(template.inputs):
            raise OperationRuntimeError(
                f"recipe={self.recipe.id} operation={join_id}: "
                f"合流端口未闭合 {sorted(matched)}")
        run = self._runs.get(parent_rows[0].run_id)
        if run is None:
            raise OperationRuntimeError(
                f"run={parent_rows[0].run_id}: 未登记的运行上下文")
        instance = self._build_instance(
            template, run=run, uid=int(uid),
            predecessors=tuple(row.instance_id for row in parent_rows),
            input_uids=tuple(int(value) for value in input_uids),
            route_id="", sim_time=sim_time)
        self.graph.add(instance)
        return instance

    def _build_instance(self, template: OperationTemplate, *,
                        run: RecipeRunContext, uid: int,
                        predecessors: tuple[str, ...],
                        input_uids: tuple[int, ...], route_id: str,
                        sim_time: float = 0.0) -> OperationInstance:
        fields = legacy_task_fields(template)
        return OperationInstance(
            instance_id=f"{run.run_id}:{int(uid)}:{template.id}",
            task_id=f"{template.kind}_uid{int(uid)}",
            template_id=template.id, kind=template.kind, uid=int(uid),
            source=fields["source"], destination=fields["destination"],
            dock=fields["dock"], device=fields["device"],
            transport=bool(fields["transport"]),
            run_id=run.run_id, recipe_id=run.recipe_id,
            recipe_version=run.recipe_version, entry_id=run.entry_id,
            selected_route=str(route_id),
            predecessor_instance_ids=list(predecessors),
            input_uids=list(input_uids),
            capability=template.capability,
            candidate_device_ids=list(template.candidate_devices),
            current_work_step=(template.work_steps[0].template_id
                               if template.work_steps else ""),
            priority=int(template.priority),
            created_sim_time=float(run.created_sim_time or sim_time))

    # ── 状态迁移 ────────────────────────────────────────────

    def mark_started(self, instance_id: str, sim_time: float, *,
                     execution_id: str = "") -> OperationInstance:
        instance = self.graph.require(instance_id)
        if instance.state not in (TaskStatus.PENDING, TaskStatus.WAITING):
            raise OperationRuntimeError(
                f"instance={instance_id}: 状态 {instance.state.value} "
                f"不能启动")
        instance.state = TaskStatus.RUNNING
        instance.phase = "RUNNING"
        instance.attempts += 1
        instance.started_sim_time = float(sim_time)
        instance.waiting_since = None
        if execution_id:
            instance.evidence.setdefault("execution_id", str(execution_id))
        return instance

    def commit_success(self, instance_id: str, *, output_uids: Iterable[int],
                       sim_time: float,
                       evidence: Optional[dict] = None) -> OperationInstance:
        """把工序标记 DONE 并登记输出; 重复提交/未解锁/已失败都拒绝."""
        instance = self.graph.require(instance_id)
        if instance.state is TaskStatus.DONE or instance.committed:
            raise OperationRuntimeError(
                f"instance={instance_id}: 重复提交 (已 DONE)")
        if instance.state is TaskStatus.FAILED:
            raise OperationRuntimeError(
                f"instance={instance_id}: 已 FAILED, 不能改写成 DONE")
        if instance.state is TaskStatus.REJECTED:
            raise OperationRuntimeError(
                f"instance={instance_id}: 已 REJECTED, 不能改写成 DONE")
        if not self.graph.unlocked(instance):
            raise OperationRuntimeError(
                f"instance={instance_id}: 前驱未全部提交, 不能提交")
        instance.state = TaskStatus.DONE
        instance.phase = "DONE"
        instance.finished_sim_time = float(sim_time)
        instance.committed_sim_time = float(sim_time)
        instance.output_uids = [int(uid) for uid in output_uids]
        if evidence:
            instance.evidence.update(evidence)
        return instance

    def mark_failed(self, instance_id: str, reason: str,
                    sim_time: float, *,
                    evidence: Optional[dict] = None) -> OperationInstance:
        """标记失败; 已终态的实例保持原状态与首个失败原因."""
        instance = self.graph.require(instance_id)
        if instance.state.terminal:
            return instance
        instance.state = TaskStatus.FAILED
        instance.phase = "FAILED"
        instance.failure_reason = str(reason)
        instance.finished_sim_time = float(sim_time)
        if evidence:
            instance.evidence.update(evidence)
        return instance

    # ── 查询 ────────────────────────────────────────────────

    def instance(self, instance_id: str) -> Optional[OperationInstance]:
        return self.graph.get(instance_id)

    def for_task_id(self, task_id: str) -> Optional[OperationInstance]:
        return self.graph.by_task_id(task_id)

    def ready(self) -> list[OperationInstance]:
        return self.graph.ready()

    def claim_ready(self, sim_time: Optional[float] = None
                    ) -> list[OperationInstance]:
        return self.graph.claim_ready(sim_time)

    def claim_instance(self, instance_id: str,
                       sim_time: Optional[float] = None
                       ) -> OperationInstance:
        """只认领一个仍 ready 的实例; 用于阶段 4 逐决策入队.

        与 ``claim_ready()`` 相同: ``created_sim_time`` 刷新为进入可派工
        队列的时间. 已派发、非 PENDING 或依赖未满足都拒绝.
        """
        instance = self.graph.require(instance_id)
        if instance.state is not TaskStatus.PENDING:
            raise OperationRuntimeError(
                f"instance={instance_id}: 状态 {instance.state.value} "
                f"不能认领派工")
        if instance.dispatched:
            raise OperationRuntimeError(
                f"instance={instance_id}: 已派发, 不能重复认领")
        if not self.graph.unlocked(instance):
            raise OperationRuntimeError(
                f"instance={instance_id}: 前驱未全部提交, 不能派工")
        instance.dispatched = True
        if sim_time is not None:
            instance.created_sim_time = float(sim_time)
        return instance

    def select_device(self, instance_id: str,
                      device_id: str) -> OperationInstance:
        """登记调度器选定设备; 必须是 recipe 候选, 启动后不得更换."""
        instance = self.graph.require(instance_id)
        allowed = list(instance.candidate_device_ids)
        if not allowed:
            raise OperationRuntimeError(
                f"instance={instance_id}: 没有 recipe 候选设备")
        if str(device_id) not in allowed:
            raise OperationRuntimeError(
                f"instance={instance_id}: 设备 {device_id!r} 不在候选 "
                f"{allowed}")
        if instance.selected_device_id:
            if instance.selected_device_id != str(device_id):
                raise OperationRuntimeError(
                    f"instance={instance_id}: 已选择设备 "
                    f"{instance.selected_device_id!r}, 不能改派 "
                    f"{device_id!r}")
            return instance
        if instance.state is TaskStatus.RUNNING:
            raise OperationRuntimeError(
                f"instance={instance_id}: 执行已启动, 不能再登记首台设备")
        instance.selected_device_id = str(device_id)
        return instance

    def record_phase(self, instance_id: str,
                     phase: str) -> OperationInstance:
        """记录执行阶段文本; 生命周期状态只能经窄迁移方法写入."""
        instance = self.graph.require(instance_id)
        instance.phase = str(phase)
        return instance

    def record_resources(self, instance_id: str,
                         resources: Iterable[str]) -> OperationInstance:
        """记录本次派工实际申请的资源集 (资源表仍是唯一所有权来源)."""
        instance = self.graph.require(instance_id)
        instance.resources = [str(name) for name in resources]
        return instance

    def record_upgraded(self, instance_id: str,
                        upgraded: bool) -> OperationInstance:
        instance = self.graph.require(instance_id)
        instance.upgraded = bool(upgraded)
        return instance

    def mark_waiting(self, instance_id: str, sim_time: float,
                     reason: str) -> OperationInstance:
        """PENDING/WAITING → WAITING; 同一原因的重复等待不重复记证据."""
        instance = self.graph.require(instance_id)
        if instance.state not in (TaskStatus.PENDING, TaskStatus.WAITING):
            raise OperationRuntimeError(
                f"instance={instance_id}: 状态 {instance.state.value} "
                f"不能等待")
        phase = f"WAITING: {reason}"
        changed = instance.state is TaskStatus.PENDING \
            or instance.phase != phase
        if instance.state is TaskStatus.PENDING:
            instance.waiting_since = float(sim_time)
        instance.state = TaskStatus.WAITING
        instance.phase = phase
        if changed:
            instance.evidence.setdefault("waits", []).append({
                "sim_time": round(float(sim_time), 4), "reason": reason})
        return instance

    def mark_blocked(self, instance_id: str, sim_time: float,
                     reason: str) -> OperationInstance:
        """非终态 → BLOCKED; 保留失败语义与已有证据."""
        instance = self.graph.require(instance_id)
        if instance.state.terminal:
            raise OperationRuntimeError(
                f"instance={instance_id}: 已结束, 不能阻塞")
        phase = f"BLOCKED: {reason}"
        changed = instance.state is not TaskStatus.BLOCKED \
            or instance.phase != phase
        instance.state = TaskStatus.BLOCKED
        instance.blocked_since = float(sim_time)
        instance.phase = phase
        if changed:
            instance.evidence.setdefault("blocks", []).append({
                "sim_time": round(float(sim_time), 4), "reason": reason})
        return instance

    def mark_resumed(self, instance_id: str, sim_time: float,
                     reason: str) -> OperationInstance:
        """BLOCKED → RUNNING; 只恢复同一执行, 不重试也不改线."""
        instance = self.graph.require(instance_id)
        if instance.state is not TaskStatus.BLOCKED:
            raise OperationRuntimeError(
                f"instance={instance_id}: 不在 BLOCKED 状态")
        instance.state = TaskStatus.RUNNING
        instance.blocked_since = None
        instance.phase = f"RESUMED: {reason}"
        instance.evidence.setdefault("resumes", []).append({
            "sim_time": round(float(sim_time), 4), "reason": reason})
        return instance

    def completed_output(self, uid: int,
                         port: MaterialPort) -> Optional[OperationInstance]:
        """该 UID 上已完成且输出端口与 ``port`` 一致的实例 (join 输入绑定)."""
        for instance in self.graph.by_uid(uid):
            if instance.state is not TaskStatus.DONE or not instance.committed:
                continue
            try:
                template = self.catalog.operation(instance.template_id,
                                                  self.recipe.id)
            except ProcessModelError:
                continue
            if _port_matches(template.output, port):
                return instance
        return None

    def join_blocked(self, template_id: str) -> bool:
        """同一合流已有未终结节点时不再创建第二个."""
        return bool(self.graph.pending_join_nodes(template_id))

    def all_done(self) -> bool:
        return self.graph.all_done()

    def validate(self) -> list[str]:
        return self.graph.validate(self.catalog, self.recipe)

    def as_dict(self) -> dict:
        issues = self.validate()
        return {
            "recipe_id": self.recipe.id,
            "recipe_version": self.recipe.version,
            "runs": [run.as_dict() for run in self._runs.values()],
            "graph": self.graph.as_dict(),
            "validation": {"ok": not issues, "errors": issues},
        }

    # ── 内部 ────────────────────────────────────────────────

    def _entry(self, entry_id: str):
        for entry in self.recipe.entries:
            if entry.id == entry_id:
                return entry
        raise OperationRuntimeError(
            f"recipe={self.recipe.id}: 未知订单入口 {entry_id!r}")
