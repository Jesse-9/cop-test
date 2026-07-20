"""
factory_robot.py — 机械臂动作动画器
==================================
本模块为视觉增强层：在工作岛物料到达/完成/装配事件触发时，驱动
CoppeliaSim 内置机械臂做抓取-移动-放置动作，使仿真在视觉上更真实。

设计原则:
  - 纯视觉叠加：不改变 factory_agv / factory_island / factory_controller
    的任何调度、节拍、异常逻辑。动画仅在 _update_scene_positions 之后
    调用，作为"锦上添花"的可视化层。
  - 关节角度插值：使用预计算关键帧 (cfg.ARM_ANIMATIONS) 线性插值，
    不依赖逆运动学，简单可靠。
  - 容错：机械臂/关节句柄缺失时静默跳过，不中断仿真。

对应架构层: Python Control Layer — RobotArmAnimator (视觉增强)
参考文档:   references/scene-building.md §Using CoppeliaSim Built-in Models

Exports:
  Class: RobotArmAnimator
"""

import logging
import math
from typing import Optional

import factory_config as cfg
from factory_types import AGVState


class RobotArmAnimator:
    """机械臂关节动画驱动器 — Robot Arm Joint Animation Driver.

    每个工作岛的机械臂维护一个动画状态机:
      IDLE → (事件触发) → PLAYING (序列帧插值) → IDLE

    动画类型 (对应 cfg.ARM_ANIMATIONS):
      pick_and_place: 物料到达岛/岛完成时触发，完整抓取-放置循环
      assemble:       E 岛装配时双臂协同
      idle_wiggle:     PROCESSING 期间机械臂轻微活动

    公共 API:
      trigger(island, anim_name)  → 触发某岛机械臂动画
      update(dt, islands, agv_scheduler) → 每步推进动画 + 自动触发

    Attributes:
        sim:     CoppeliaSim Remote API "sim" 模块.
        logger:  日志记录器.
        arms:    岛名 → 臂状态字典 {handle, joints, anim, frame_t, ...}.
    """

    def __init__(self, sim, logger: logging.Logger):
        """初始化动画器并采集各机械臂关节句柄。

        Args:
            sim:    CoppeliaSim Remote API "sim" 模块 (已连接).
            logger: 日志记录器.
        """
        self.sim = sim
        self.logger = logger
        # arms[island_key] = {
        #   "root": handle, "joints": [h1, h2, ...],
        #   "anim": anim_name or None, "frame": 当前帧索引,
        #   "frame_t": 当前帧内已用时, "n_joints": 关节数
        # }
        self.arms: dict[str, dict] = {}
        self._collect_arm_joints()

    # ── 初始化：采集关节句柄 ───────────────────────────────────

    def _collect_arm_joints(self):
        """采集各机械臂根句柄与其关节句柄。

        CoppeliaSim 命名不支持字符 '/', 项目内所有别名一律用下划线连接
        (见 CLAUDE.md「已知问题与坑」第 1 条)。此处遍历所有对象按叶子别名
        匹配机械臂根, 再用 getObjectsInTree 枚举其关节子树。

        匹配规则: 对象别名 (getObjectAlias) 的叶子部分等于
        ISLAND_ROBOTS[<key>]["alias"] 的叶子部分。
        """
        sim = self.sim
        # 预计算各机械臂要匹配的叶子别名
        # alias 形如 "Island_A_Assembly_Robot_A"，叶子为 "Robot_A"
        # setObjectAlias 后存储别名即为该完整名, 也可能为内置模型残留的原生别名,
        # 故同时匹配归一化全名与叶子名。
        leaf_map: dict[str, str] = {}  # key -> (leaf, full_alias)
        for key, robot_cfg in cfg.ISLAND_ROBOTS.items():
            full = robot_cfg["alias"]
            # 叶子取末两段, 如 "Island_A_Assembly_Robot_A" -> "Robot_A"
            parts = full.split("_")
            leaf = "_".join(parts[-2:]) if len(parts) >= 2 else full
            leaf_map[key] = (leaf, full)

        # 收集所有对象 (shape + dummy + joint 类型) 的 (handle, alias)
        all_handles: list[tuple[int, str]] = []
        for tval in (sim.object_shape_type, sim.object_dummy_type,
                     sim.object_joint_type):
            i = 0
            while True:
                try:
                    h = sim.getObjects(i, tval)
                    try:
                        a = sim.getObjectAlias(h)
                    except Exception:
                        a = ""
                    all_handles.append((h, a))
                    i += 1
                except Exception:
                    break
                if i > 2000:
                    break

        # 为每个机械臂键找根句柄
        for key, (leaf, full) in leaf_map.items():
            root_handle = None
            for h, a in all_handles:
                if not a:
                    continue
                # 精确匹配完整别名, 或叶子名
                if a == full or a == leaf or a.endswith("_" + leaf):
                    root_handle = h
                    break
            if root_handle is None:
                self.logger.debug(
                    f"[ROBOT] 机械臂 {leaf} 未在场景中找到，跳过动画"
                )
                continue

            # 枚举机械臂子树的关节
            joints = []
            try:
                tree = sim.getObjectsInTree(root_handle, sim.handle_all, 0)
                # 先找出所有 "Gripper" 子树的根，其子树内的关节属于夹爪，
                # 不应被机械臂动画驱动（夹爪有自己的开合逻辑）
                gripper_subtrees: list[int] = []
                for obj in tree:
                    try:
                        if sim.getObjectAlias(obj) == "Gripper":
                            gripper_subtrees.append(obj)
                    except Exception:
                        continue
                gripper_joint_handles: set[int] = set()
                for g_root in gripper_subtrees:
                    try:
                        g_tree = sim.getObjectsInTree(g_root, sim.handle_all, 0)
                        for g_obj in g_tree:
                            if sim.getObjectType(g_obj) == sim.object_joint_type:
                                gripper_joint_handles.add(int(g_obj))
                    except Exception:
                        continue
                # 收集机械臂关节 (排除夹爪关节)
                for obj in tree:
                    try:
                        if (sim.getObjectType(obj) == sim.object_joint_type
                                and int(obj) not in gripper_joint_handles):
                            joints.append(obj)
                    except Exception:
                        continue
            except Exception as e:
                self.logger.debug(
                    f"[ROBOT] {leaf} 子树枚举失败: {e}"
                )

            self.arms[key] = {
                "root": root_handle,
                "joints": joints,
                "n_joints": len(joints),
                "anim": None,
                "frame": 0,
                "frame_t": 0.0,
            }
            self.logger.info(
                f"[ROBOT] {leaf} 已就绪: {len(joints)} 个关节"
            )

    # ── 公共接口 ─────────────────────────────────────────────

    def trigger(self, island_key: str, anim_name: str):
        """触发某机械臂播放指定动画 — Trigger an animation.

        Args:
            island_key: cfg.ISLAND_ROBOTS 的键 (如 "A", "E_C").
            anim_name:  cfg.ARM_ANIMATIONS 的键 (如 "pick_and_place").
        """
        arm = self.arms.get(island_key)
        if arm is None or arm["n_joints"] == 0:
            return
        if anim_name not in cfg.ARM_ANIMATIONS:
            return
        arm["anim"] = anim_name
        arm["frame"] = 0
        arm["frame_t"] = 0.0

    def update(self, dt: float, islands: dict, agv_scheduler):
        """每步推进所有机械臂动画 — Advance all arm animations by dt.

        自动触发逻辑:
          - 岛进入 PROCESSING: 触发 idle_wiggle
          - 岛进入 COMPLETE: 触发 pick_and_place (抓取送出)
          - AGV 到达/离开 + 装配事件: 由控制器显式 trigger

        Args:
            dt:           时间增量 (秒).
            islands:      岛名 → IslandController 映射.
            agv_scheduler: AGVScheduler 实例 (用于判断 AGV 状态).
        """
        # 1. 自动触发: 岛状态变化
        for name, ctrl in islands.items():
            arm_key = name if name != "E" else None
            # E 岛动画由装配事件显式触发，不在此自动处理
            if arm_key and arm_key in self.arms:
                # PROCESSING → idle_wiggle (持续)
                if ctrl.state.name == "PROCESSING":
                    arm = self.arms[arm_key]
                    if arm["anim"] is None:
                        self.trigger(arm_key, "idle_wiggle")

        # 2. 推进所有正在播放的动画
        for key, arm in self.arms.items():
            self._step_arm(arm, dt)

    def on_assembly(self):
        """E 岛装配完成时调用 — 双臂协同装配动画。"""
        self.trigger("E_C", "assemble")
        self.trigger("E_D", "assemble")

    # ── 内部方法 ─────────────────────────────────────────────

    def _step_arm(self, arm: dict, dt: float):
        """推进单个机械臂的动画帧插值 — Step one arm's animation."""
        if arm["anim"] is None or arm["n_joints"] == 0:
            return

        anim = cfg.ARM_ANIMATIONS[arm["anim"]]
        n_frames = len(anim)
        if n_frames == 0:
            arm["anim"] = None
            return

        arm["frame_t"] += dt
        # 单帧插值完成 → 前进到下一帧
        while arm["frame_t"] >= cfg.ARM_FRAME_DURATION:
            arm["frame_t"] -= cfg.ARM_FRAME_DURATION
            arm["frame"] += 1
            # 序列播放完毕
            if arm["frame"] >= n_frames - 1:
                arm["frame"] = n_frames - 1
                # 回到末帧后结束 (idle_wiggle 循环)
                if arm["anim"] == "idle_wiggle":
                    arm["frame"] = 0
                else:
                    # 持续保持末帧直到下次触发
                    arm["anim"] = None
                break

        # 计算插值因子
        if arm["anim"] is None:
            return
        t = arm["frame_t"] / cfg.ARM_FRAME_DURATION
        t = max(0.0, min(1.0, t))
        f0 = anim[arm["frame"]]
        f1 = anim[min(arm["frame"] + 1, n_frames - 1)]

        # 按实际关节数截断/补零
        nj = arm["n_joints"]
        angles = []
        for i in range(nj):
            a0 = f0[i] if i < len(f0) else 0.0
            a1 = f1[i] if i < len(f1) else 0.0
            angles.append(a0 + (a1 - a0) * t)

        # 驱动关节
        sim = self.sim
        for i, jh in enumerate(arm["joints"]):
            try:
                sim.setJointPosition(jh, angles[i])
            except Exception:
                continue
