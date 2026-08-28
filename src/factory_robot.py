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

import factory_config as cfg
from factory_scene import find_by_alias, parse_leaf


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
        (见 CLAUDE.md「已知问题与坑」第 1 条)。此处用 factory_scene.find_by_alias
        统一匹配 (完整别名 / 叶子 / _前缀) — 取代旧内联 3 重循环。

        匹配规则: 对象别名 (getObjectAlias) 与 ISLAND_ROBOTS[<key>]["alias"]
        完整相等, 或叶子部分相等, 或以 _<leaf> 结尾 (alias 路径 quirk)。
        """
        sim = self.sim
        # 为每个机械臂键找根句柄 (统一 helper, 消除 wiki 02-01 指出的重复)
        for key, robot_cfg in cfg.ISLAND_ROBOTS.items():
            full = robot_cfg["alias"]
            leaf = parse_leaf(full, 2)
            root_handle = find_by_alias(
                sim, full,
                sim.object_shape_type, sim.object_dummy_type,
            )
            if root_handle < 0:
                self.logger.debug(
                    f"[ROBOT] 机械臂 {leaf} 未在场景中找到，跳过动画"
                )
                continue

            # 枚举机械臂子树的关节
            joints = []
            tree = None
            try:
                tree = sim.getObjectsInTree(root_handle, sim.handle_all, 0)
            except Exception as e:
                # 实测: 仿真未启动或模型子树含不可查询对象时,
                # getObjectsInTree 抛 "Operation cannot be accomplished in
                # current state"。此时退化为全场景扫描, 按 "joint" 类型
                # + 别名匹配机械臂所属关节 (UR5 关节别名通常含 robot 别名)。
                self.logger.warning(
                    f"[ROBOT] {leaf} 子树枚举失败 ({type(e).__name__}: {e}), "
                    "退化为全场景关节扫描"
                )

            if tree is not None:
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
            else:
                # 回退路径: 子树枚举失败 → 全场景按关节类型扫描,
                # 仅收别名匹配该机械臂的关节 (UR5 关节常命名为
                # "<RobotAlias>_jointN" 或 "UR5_jointN")。
                i = 0
                while i < 5000:
                    try:
                        jh = sim.getObjects(i, sim.object_joint_type)
                    except Exception:
                        break
                    if jh == -1:
                        break
                    try:
                        ja = sim.getObjectAlias(jh)
                    except Exception:
                        ja = ""
                    # 关节别名含机械臂叶子名, 或属该臂根子树 (父链回溯到 root)
                    if ja and (leaf in ja or full in ja
                               or self._is_descendant(sim, jh, root_handle)):
                        joints.append(jh)
                    i += 1

            # API 不承诺 getObjectsInTree 的返回顺序。UR5 六个本体关节
            # 沿单一父子链排列，按相对根节点深度排序后才能与关键帧稳定对应。
            joints = sorted(
                set(int(jh) for jh in joints),
                key=lambda jh: self._depth_from_root(sim, jh, root_handle),
            )[:6]
            self.arms[key] = {
                "root": root_handle,
                "joints": joints,
                "n_joints": len(joints),
                "anim": None,
                "frame": 0,
                "frame_t": 0.0,
            }
            if len(joints) == 0:
                self.logger.warning(
                    f"[ROBOT] {leaf} 未采集到任何关节, 动画将禁用 "
                    "(检查机械臂模型是否加载成功 / 仿真是否已启动)"
                )
            else:
                self.logger.info(
                    f"[ROBOT] {leaf} 已就绪: {len(joints)} 个关节"
                )
                # 置初态为 HOME 姿态, 避免机械臂以模型默认奇异姿态出现
                self._set_home_pose(self.arms[key])

    @staticmethod
    def _is_descendant(sim, handle: int, root_handle: int,
                       max_depth: int = 20) -> bool:
        """判断 handle 是否为 root_handle 的后代 — Walk parent chain."""
        cur = handle
        for _ in range(max_depth):
            try:
                parent = sim.getObjectParent(cur)
            except Exception:
                return False
            if parent == -1 or parent == cur:
                return False
            if parent == root_handle:
                return True
            cur = parent
        return False

    @staticmethod
    def _depth_from_root(sim, handle: int, root_handle: int,
                         max_depth: int = 100) -> int:
        """返回对象相对根节点的层级深度，无法回溯时排到末尾。"""
        cur = handle
        for depth in range(1, max_depth + 1):
            try:
                parent = sim.getObjectParent(cur)
            except Exception:
                return max_depth + 1
            if parent == root_handle:
                return depth
            if parent < 0 or parent == cur:
                return max_depth + 1
            cur = parent
        return max_depth + 1

    def _set_home_pose(self, arm: dict):
        """置机械臂为 HOME 待机姿态 — Set joints to ARM_ANIMATIONS HOME frame.

        UR5 的 HOME 帧 (pick_and_place[0]) 是标准 L 形待机位, 立即下发
        使机械臂在仿真启动瞬间就处于合理姿态, 而非模型默认的竖直奇异位。
        """
        home = cfg.ARM_ANIMATIONS.get("pick_and_place")
        if not home or arm["n_joints"] == 0:
            return
        f0 = home[0]
        self._command_pose(arm, f0)

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
          - 岛进入 PROCESSING: 触发 idle_wiggle (仅在 anim is None 时, 不会覆盖
            pick_and_place / assemble 等显式动作)
          - 岛完成出料: 由控制器显式触发 pick_and_place (见
            FactoryController._schedule_agv_tasks / _handle_ac_transfer /
            _handle_ce_transfer)
          - E 岛装配事件: 由控制器显式触发 on_assembly (双臂协同)

        Args:
            dt:           时间增量 (秒).
            islands:      岛名 → IslandController 映射.
            agv_scheduler: AGVScheduler 实例 (用于判断 AGV 状态).
        """
        # 1. 自动触发: 岛进入 PROCESSING 状态时, 启动 idle_wiggle 循环。
        #    注意: 仅当 anim is None 时才置, 因此 pick_and_place / assemble 等
        #    显式动作不会被 idle_wiggle 打断 (test_idle_wiggle_does_not_interrupt_pick_and_place)。
        for name, ctrl in islands.items():
            arm_key = name if name != "E" else None
            # E 岛动画由装配事件显式触发，不在此自动处理
            if arm_key and arm_key in self.arms:
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
                    # 先精确下发末帧，再结束。旧实现先清 anim 后 return，
                    # 导致最后一帧永远只逼近、不真正到达。
                    self._command_pose(arm, anim[-1])
                    arm["anim"] = None
                    return

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

        self._command_pose(arm, angles)

    def _command_pose(self, arm: dict, angles: list[float]):
        """向 UR5 动态位置控制器下发目标，旧模型回退为直接设位。"""
        sim = self.sim
        for i, jh in enumerate(arm["joints"]):
            angle = angles[i] if i < len(angles) else 0.0
            try:
                sim.setJointTargetPosition(jh, angle)
            except Exception:
                try:
                    sim.setJointPosition(jh, angle)
                except Exception:
                    continue
