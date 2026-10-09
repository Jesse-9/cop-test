"""
factory_robot.py — 机械臂设备层与动作动画器
==========================================
两个层次, 职责分离 (重构方案 §9.3 / §9.4):

  RobotArmDevice    设备层: 定位根对象/六轴/TCP/夹爪, 记录并设置关节控制模式,
                    下发目标位置, 读回实际位置与目标位置并给出最大误差。
                    只回答"怎么让它动、动了多少", 不含任何业务语义。
  RobotArmAnimator  视觉层: 按 cfg.ARM_ANIMATIONS 关键帧插值驱动动作,
                    每个岛持有自己的 RobotArmDevice。

设计原则:
  - 纯视觉叠加：不改变 factory_agv / factory_island / factory_controller
    的任何调度、节拍、异常逻辑。动画仅在 _update_scene_positions 之后
    调用，作为"锦上添花"的可视化层。
  - 关节角度插值：使用预计算关键帧 (cfg.ARM_ANIMATIONS) 线性插值，
    不依赖逆运动学，简单可靠。
  - 容错：机械臂/关节句柄缺失、模式 API 不可用时降级或静默跳过，不中断仿真。

对应架构层: Python Control Layer — RobotArmDevice (设备) + RobotArmAnimator (视觉)
参考文档:   references/scene-building.md §Using CoppeliaSim Built-in Models

Exports:
  Class: RobotArmDevice, RobotArmAnimator
"""

import logging

import factory_config as cfg
from factory_scene import find_by_alias, parse_leaf


class RobotArmDevice:
    """单台机械臂设备层 — Single-arm device abstraction (重构方案 §9.3).

    每台臂一个实例, 持有根/六轴/TCP/夹爪句柄与关节模式记录。

    Attributes:
        island_key:     cfg.ISLAND_ROBOTS 的键 (如 "A" / "E_C").
        root_handle:    机械臂根对象句柄, 未找到为 -1.
        joints:         六个本体关节句柄, 按父链深度 (base→tip) 排序.
        tcp_handle:     末端 TCP 句柄 (connection/tip/flange...), 未找到为 -1.
        gripper_handle: 夹爪根句柄, 未挂夹爪为 -1.
        control_mode:   最近一次成功应用的控制模式名, 未应用过为 None.
        joint_modes:    最近一次读回的关节模式列表 (读不到时为 -1).
    """

    #: 控制模式名 → sim 常量属性名 (4.10 实测: kinematic=0/dependent=4/dynamic=5)
    _MODE_CONST = {
        "kinematic": "jointmode_kinematic",
        "dependent": "jointmode_dependent",
        "dynamic": "jointmode_dynamic",
    }

    def __init__(self, sim, island_key: str, cfg_entry: dict,
                 logger: logging.Logger):
        """定位单台机械臂并读回其当前状态 (不改动场景)。

        Args:
            sim:        CoppeliaSim Remote API "sim" 模块 (已连接).
            island_key: cfg.ISLAND_ROBOTS 的键.
            cfg_entry:  cfg.ISLAND_ROBOTS[island_key] 配置项 (需含 "alias").
            logger:     日志记录器.
        """
        self.sim = sim
        self.logger = logger
        self.island_key = island_key
        self.alias = cfg_entry.get("alias", "")
        self.leaf = parse_leaf(self.alias, 2)
        self.root_handle = -1
        self.joints: list[int] = []
        self.tcp_handle = -1
        self.gripper_handle = -1
        self.control_mode: str | None = None
        self.joint_modes: list[int] = []
        self._locate()

    @property
    def enabled(self) -> bool:
        """根对象与六个本体关节是否都已就位 — Device usable?"""
        return self.root_handle >= 0 and len(self.joints) > 0

    # ── 定位：根 / 关节 / TCP / 夹爪 ─────────────────────────────

    def _locate(self):
        """查找根对象、本体关节、TCP 与夹爪并记录当前关节模式。

        CoppeliaSim 命名不支持字符 '/', 项目内所有别名一律用下划线连接
        (见 CLAUDE.md「已知问题与坑」第 1 条)。此处用 factory_scene.find_by_alias
        统一匹配 (完整别名 / 叶子 / _前缀)。
        """
        sim = self.sim
        root_handle = find_by_alias(
            sim, self.alias,
            sim.object_shape_type, sim.object_dummy_type,
        )
        if root_handle < 0:
            self.logger.debug(
                f"[ROBOT] 机械臂 {self.leaf} 未在场景中找到，跳过动画"
            )
            return
        self.root_handle = root_handle
        self.joints = self._collect_body_joints()
        self.gripper_handle = self._find_gripper_root()
        self.tcp_handle = self._find_tcp()
        self.joint_modes = self.read_modes()

        if not self.joints:
            self.logger.warning(
                f"[ROBOT] {self.leaf} 未采集到任何关节, 动画将禁用 "
                "(检查机械臂模型是否加载成功 / 仿真是否已启动)"
            )
        else:
            self.logger.info(
                f"[ROBOT] {self.leaf} 已就绪: {len(self.joints)} 个关节 "
                f"(TCP={'有' if self.tcp_handle >= 0 else '无'}, "
                f"夹爪={'有' if self.gripper_handle >= 0 else '无'}, "
                f"模式={self.joint_modes})"
            )

    def _subtree(self) -> list[int]:
        """返回根对象子树句柄列表; 枚举失败返回空列表。

        实测: 仿真未启动或模型子树含不可查询对象时, getObjectsInTree 抛
        "Operation cannot be accomplished in current state"。
        """
        try:
            return list(self.sim.getObjectsInTree(
                self.root_handle, self.sim.handle_all, 0
            ))
        except Exception:
            return []

    def _collect_body_joints(self) -> list[int]:
        """采集六个本体关节 — Collect the 6 UR5 body joints (base→tip).

        排除 Gripper 子树内的关节 (夹爪有独立开合逻辑, 不应被本体动画驱动)。
        子树枚举失败时退化为全场景关节扫描, 按别名/父链归属该臂。
        """
        sim = self.sim
        tree = self._subtree()
        joints: list[int] = []

        if tree:
            # 先找出所有 "Gripper" 子树的根, 其子树内的关节属于夹爪
            gripper_joint_handles: set[int] = set()
            for obj in tree:
                try:
                    if sim.getObjectAlias(obj) != cfg.ARM_GRIPPER_ALIAS:
                        continue
                    for g_obj in sim.getObjectsInTree(
                        obj, sim.handle_all, 0
                    ):
                        try:
                            if sim.getObjectType(g_obj) == sim.object_joint_type:
                                gripper_joint_handles.add(int(g_obj))
                        except Exception:
                            continue
                except Exception:
                    continue
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
                if ja and (self.leaf in ja or self.alias in ja
                           or self._is_descendant(sim, jh, self.root_handle)):
                    joints.append(jh)
                i += 1

        # API 不承诺 getObjectsInTree 的返回顺序。UR5 六个本体关节
        # 沿单一父子链排列，按相对根节点深度排序后才能与关键帧稳定对应。
        return sorted(
            set(int(jh) for jh in joints),
            key=lambda jh: self._depth_from_root(sim, jh, self.root_handle),
        )[:6]

    def _find_gripper_root(self) -> int:
        """在子树中查找夹爪根对象 (别名 == cfg.ARM_GRIPPER_ALIAS)。"""
        for obj in self._subtree():
            try:
                if self.sim.getObjectAlias(obj) == cfg.ARM_GRIPPER_ALIAS:
                    return int(obj)
            except Exception:
                continue
        return -1

    def _find_tcp(self) -> int:
        """在子树中查找末端 TCP — Locate the tool center point (§9.3).

        按 cfg.ARM_TCP_ALIASES 的叶子名匹配 (4.10 内置 UR5.ttm 的末端是
        "connection", 实测为 force sensor 而非 dummy)。命中的对象中优先取
        force sensor / dummy, 其次才是 shape (link 的可见件也叫 tip 类名字)。
        """
        candidates: list[int] = []
        for obj in self._subtree():
            try:
                alias = self.sim.getObjectAlias(obj)
            except Exception:
                continue
            leaf = alias.rsplit("_", 1)[-1]
            if alias in cfg.ARM_TCP_ALIASES or leaf in cfg.ARM_TCP_ALIASES:
                candidates.append(int(obj))
        if not candidates:
            return -1
        preferred = (
            getattr(self.sim, "object_forcesensor_type", -100),
            self.sim.object_dummy_type,
        )
        for want in preferred:
            for obj in candidates:
                try:
                    if self.sim.getObjectType(obj) == want:
                        return obj
                except Exception:
                    continue
        return candidates[0]

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

    # ── 控制模式 (§9.2) ─────────────────────────────────────────

    def read_modes(self) -> list[int]:
        """读回六轴当前关节模式 — Read joint modes (读不到记 -1)。

        4.10 实测: `sim.getJointMode` 返回 **(jointMode, options) 二元组**,
        与手册的单一返回值签名不符 —— 必须归一化, 否则 `int(元组)` 抛
        TypeError 被兜底吞成 -1, 模式写回校验会静默失效。
        """
        modes = []
        for jh in self.joints:
            try:
                value = self.sim.getJointMode(jh)
                if isinstance(value, (tuple, list)):
                    value = value[0] if value else -1
                modes.append(int(value))
            except Exception:
                modes.append(-1)
        return modes

    def apply_control_mode(self, mode: str) -> bool:
        """把六个本体关节切到指定模式 — Apply joint control mode.

        仅作用于本体关节; 夹爪关节不在 self.joints 内, 不会被误设。
        写回校验: 设置后立即读回, 不一致时告警 (4.10 上模型自带的
        dynamic+position control 不会静默保留)。

        Args:
            mode: "kinematic" / "dynamic" / "dependent".

        Returns:
            True 表示设置并校验通过 (或不一致但已告警)。
        """
        if not self.enabled:
            return False
        const_attr = self._MODE_CONST.get(mode)
        if const_attr is None:
            self.logger.warning(
                f"[ROBOT] {self.leaf} 未知控制模式 '{mode}', 保持原模式"
            )
            return False
        try:
            const = getattr(self.sim, const_attr)
            for jh in self.joints:
                self.sim.setJointMode(jh, const)
        except Exception as e:
            self.logger.warning(
                f"[ROBOT] {self.leaf} 切换到 {mode} 模式失败 "
                f"({type(e).__name__}: {e}), 保持原模式"
            )
            return False

        self.control_mode = mode
        self.joint_modes = self.read_modes()
        if any(m < 0 for m in self.joint_modes):
            self.logger.warning(
                f"[ROBOT] {self.leaf} 关节模式读回失败 (getJointMode 不可用), "
                f"{mode} 模式已下发但无法校验"
            )
        elif any(m != const for m in self.joint_modes):
            self.logger.warning(
                f"[ROBOT] {self.leaf} 模式写回校验不一致: "
                f"{self.joint_modes} (期望 {const})"
            )
        else:
            self.logger.info(
                f"[ROBOT] {self.leaf} 控制模式 = {mode} (写回校验通过)"
            )
        return True

    # ── 目标下发与反馈 (§9.3 / §9.4 完成判据基础) ────────────────

    def command(self, angles: list[float],
                motion_params: list[float] | None = None) -> bool:
        """下发整组关节目标位置 — Command a full six-joint target pose.

        Args:
            angles:        六个关节目标角 (rad), 不足按 0.0 补齐.
            motion_params: [maxVel, maxAccel, maxJerk], 仅 kinematic 模式生效
                           (4.10 sim.setJointTargetPosition 的第三参数);
                           None 时用关节自身的 maxVelAccelJerk 属性。

        Returns:
            True 表示设备可用并已下发。
        """
        if not self.enabled:
            return False
        sim = self.sim
        for i, jh in enumerate(self.joints):
            angle = angles[i] if i < len(angles) else 0.0
            try:
                if motion_params:
                    try:
                        sim.setJointTargetPosition(jh, angle, motion_params)
                    except TypeError:
                        # 旧版客户端不支持第三参数 → 退回关节默认运动轮廓
                        sim.setJointTargetPosition(jh, angle)
                else:
                    sim.setJointTargetPosition(jh, angle)
            except Exception:
                # 旧运动学模型回退: 直接设位
                try:
                    sim.setJointPosition(jh, angle)
                except Exception:
                    continue
        return True

    def command_joint(self, index: int, angle: float,
                      motion_params: list[float] | None = None) -> bool:
        """下发单关节目标位置 — Command a single joint (阶段一单关节验证用)."""
        if not self.enabled or not 0 <= index < len(self.joints):
            return False
        angles = self.read_targets()
        while len(angles) < len(self.joints):
            angles.append(0.0)
        angles[index] = angle
        return self.command(angles, motion_params)

    def read_positions(self) -> list[float]:
        """读回六轴实际位置 (rad) — Read actual joint positions."""
        positions = []
        for jh in self.joints:
            try:
                positions.append(float(self.sim.getJointPosition(jh)))
            except Exception:
                positions.append(0.0)
        return positions

    def read_targets(self) -> list[float]:
        """读回六轴目标位置 (rad) — Read commanded joint targets."""
        targets = []
        for jh in self.joints:
            try:
                targets.append(float(self.sim.getJointTargetPosition(jh)))
            except Exception:
                targets.append(0.0)
        return targets

    def max_error(self) -> float:
        """六轴实际位置与目标位置的最大偏差 (rad) — Max joint error.

        §9.4 的到位判据 (max|current - target| < tolerance) 直接用它。
        """
        return max(
            (abs(c - t) for c, t in
             zip(self.read_positions(), self.read_targets())),
            default=0.0,
        )


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
        devices: 岛名 → RobotArmDevice (设备层).
        arms:    岛名 → 动画状态字典 {device, joints, anim, frame, frame_t}.
    """

    def __init__(self, sim, logger: logging.Logger):
        """初始化动画器并采集各机械臂关节句柄。

        Args:
            sim:    CoppeliaSim Remote API "sim" 模块 (已连接).
            logger: 日志记录器.
        """
        self.sim = sim
        self.logger = logger
        self.devices: dict[str, RobotArmDevice] = {}
        # arms[island_key] = {
        #   "device": RobotArmDevice, "joints": [h1, h2, ...],
        #   "anim": anim_name or None, "frame": 当前帧索引,
        #   "frame_t": 当前帧内已用时, "n_joints": 关节数
        # }
        self.arms: dict[str, dict] = {}
        self._collect_arm_joints()

    # ── 初始化：采集关节句柄 ───────────────────────────────────

    def _collect_arm_joints(self):
        """为每个岛建立 RobotArmDevice 并配置控制模式 + HOME 初态。

        控制模式在设备就绪后立即下发 (§9.7): 同一关节不能既由 Python 又由
        模型脚本控制 —— 模型脚本已在 build_scene 中剥离, 故此处直接设模式。
        """
        for key, robot_cfg in cfg.ISLAND_ROBOTS.items():
            device = RobotArmDevice(self.sim, key, robot_cfg, self.logger)
            if not device.enabled:
                continue
            device.apply_control_mode(cfg.ARM_CONTROL_MODE)
            self.devices[key] = device
            arm = {
                "device": device,
                "joints": device.joints,
                "n_joints": len(device.joints),
                "anim": None,
                "frame": 0,
                "frame_t": 0.0,
            }
            self.arms[key] = arm
            # 置初态为 HOME 姿态, 避免机械臂以模型默认奇异姿态出现
            self._set_home_pose(arm)

    def _set_home_pose(self, arm: dict):
        """置机械臂为 HOME 待机姿态 — Set joints to ARM_ANIMATIONS HOME frame.

        UR5 的 HOME 帧 (pick_and_place[0]) 是标准 L 形待机位, 立即下发
        使机械臂在仿真启动瞬间就处于合理姿态, 而非模型默认的竖直奇异位。
        """
        home = cfg.ARM_ANIMATIONS.get("pick_and_place")
        if not home or arm["n_joints"] == 0:
            return
        self._command_pose(arm, home[0])

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
        """下发整组关节目标 — Delegates to the device layer."""
        arm["device"].command(angles)
