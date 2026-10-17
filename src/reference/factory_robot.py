"""
factory_robot.py — 机械臂设备层与动作动画器
==========================================
两个层次, 职责分离 (重构方案 §9.3 / §9.4):

  RobotArmDevice    设备层: 定位根对象/六轴/TCP/吸盘, 记录并设置关节控制模式,
                    下发目标位置, 读回实际位置与目标位置并给出最大误差。
                    只回答"怎么让它动、动了多少", 不含任何业务语义。
  RobotArmAnimator  任务层: 以 TCP 目标调用 simIK/阻尼最小二乘 IK，
                    每个岛持有自己的 RobotArmDevice；无 TCP 时才回退关键帧。

设计原则:
  - 纯视觉叠加：不改变 factory_agv / factory_island / factory_controller
    的任何调度、节拍、异常逻辑。动画仅在 _update_scene_positions 之后
    调用，作为"锦上添花"的可视化层。
  - TCP 目标优先：抓取和放置通过逆运动学求关节角，关键帧仅作为离线/兼容回退。
  - 容错：机械臂/关节句柄缺失、模式 API 不可用时降级或静默跳过，不中断仿真。

对应架构层: Python Control Layer — RobotArmDevice (设备) + RobotArmAnimator (视觉)
参考文档:   references/scene-building.md §Using CoppeliaSim Built-in Models

Exports:
  Class: RobotArmDevice, RobotArmAnimator
"""

import logging
import math

import reference.factory_config as cfg
from reference.factory_scene import find_by_alias, parse_leaf


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
        joint_info:     每个本体关节的 {handle, alias, parent_alias, mode} (启动诊断).
    """

    #: 控制模式名 → sim 常量属性名 (4.10 实测: kinematic=0/dependent=4/dynamic=5)
    _MODE_CONST = {
        "kinematic": "jointmode_kinematic",
        "dependent": "jointmode_dependent",
        "dynamic": "jointmode_dynamic",
    }

    def __init__(self, sim, island_key: str, cfg_entry: dict,
                 logger: logging.Logger, sim_ik=None):
        """定位单台机械臂并读回其当前状态 (不改动场景)。

        Args:
            sim:        CoppeliaSim Remote API "sim" 模块 (已连接).
            island_key: cfg.ISLAND_ROBOTS 的键.
            cfg_entry:  cfg.ISLAND_ROBOTS[island_key] 配置项 (需含 "alias").
            logger:     日志记录器.
        """
        self.sim = sim
        self.sim_ik = sim_ik
        self.logger = logger
        self.island_key = island_key
        self.alias = cfg_entry.get("alias", "")
        self.leaf = parse_leaf(self.alias, 2)
        self.root_handle = -1
        self.joints: list[int] = []
        self.tcp_handle = -1
        # 兼容旧字段名：这里代表末端工具（吸盘），不是机械夹爪。
        self.gripper_handle = -1
        self.suction_handle = -1
        self.tool_handle = -1
        # 持久化 IK 上下文 (方案 §7) — 由 ensure_ik_context() 惰性建立一次
        self.ik_context: dict | None = None
        self._ik_target_handle = -1
        self.control_mode: str | None = None
        self.joint_modes: list[int] = []
        self.joint_info: list[dict] = []
        self._locate()

    @property
    def enabled(self) -> bool:
        """根对象与**恰好六个**本体关节是否都已就位 — Device usable?

        §9.3 要求"最终必须校验恰好得到六个本体关节"。少于六个时只驱动
        采集到的那几个轴, 整臂会静默乱动; 多于六个说明采集越界。两种情况
        都判为不可用 (并告警), 而不是带着半个臂继续跑。
        """
        return (self.root_handle >= 0
                and len(self.joints) == cfg.ARM_BODY_JOINT_COUNT)

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
        self.suction_handle = self.gripper_handle
        self.tool_handle = self.suction_handle
        self.tcp_handle = self._find_tcp()
        self.joint_modes = self.read_modes()
        self.joint_info = self._describe_joints()
        # 持久化 IK target dummy — 只建一次 (方案 §7: 禁止每周期重建 IK 环境)
        self._ik_target_handle = self._create_ik_target()
        # 吸盘自带的接近传感器 (方案 §6.3 第 1 条) — 静默检测用
        self.suction_sensor_handle = self._find_suction_sensor()

        want = cfg.ARM_BODY_JOINT_COUNT
        if len(self.joints) != want:
            self.logger.warning(
                f"[ROBOT] {self.leaf} 本体关节数异常: 采集到 {len(self.joints)} 个, "
                f"要求恰好 {want} 个 —— 该臂已禁用, 动画将跳过 "
                "(检查机械臂模型是否加载成功 / 仿真是否已启动)"
            )
        else:
            self.logger.info(
                f"[ROBOT] {self.leaf} 已就绪: {len(self.joints)} 个关节 "
                f"(TCP={'有' if self.tcp_handle >= 0 else '无'}, "
                f"夹爪={'有' if self.gripper_handle >= 0 else '无'}, "
                f"模式={self.joint_modes})"
            )
            # §9.3: 输出每个关节的别名、父对象和模式 (文件日志 DEBUG 级)
            self.logger.debug(
                f"[ROBOT] {self.leaf} 六轴诊断: " + " | ".join(
                    f"{i['alias'] or i['handle']}(parent={i['parent_alias']}, "
                    f"mode={i['mode']})" for i in self.joint_info
                )
            )

    def _find_suction_sensor(self) -> int:
        """吸盘自带的接近传感器句柄 — Suction proximity sensor.

        方案 §6.3 第 1 条: 用它做 `sim.checkProximitySensor(sensor, material)`
        静默检测 (不检查 detectable 标志)。找不到返回 -1, 此时吸附退化为
        纯位姿判据并由调用方告警。
        """
        if self.suction_handle < 0:
            return -1
        sim = self.sim
        proxy_type = getattr(sim, "object_proximitysensor_type", None)
        for obj in [self.suction_handle] + self._subtree_of(self.suction_handle):
            try:
                if proxy_type is not None and sim.getObjectType(obj) != proxy_type:
                    continue
                if sim.getObjectAlias(obj) == cfg.SUCTION_SENSOR_ALIAS:
                    return int(obj)
            except Exception:
                continue
        return -1

    def _subtree_of(self, root: int) -> list[int]:
        """root 的整棵子树句柄列表 (不含 root)。"""
        try:
            return [int(h) for h in
                    self.sim.getObjectsInTree(root, self.sim.handle_all, 0)]
        except Exception:
            return []

    def _create_ik_target(self) -> int:
        """创建这台臂专用的 IK target dummy — Create the persistent IK target.

        方案 §7: target 只在初始化时创建一次, 之后每个控制周期只更新它的
        世界位姿再调用 handleGroup。建不出来返回 -1 (该臂 IK 不可用)。
        """
        sim = self.sim
        if self.tcp_handle < 0:
            return -1
        try:
            handle = sim.createDummy(0.01)
            sim.setObjectAlias(handle, f"{self.alias}_IKTarget")
            pose = sim.getObjectPose(self.tcp_handle, sim.handle_world)
            sim.setObjectPose(handle, pose, sim.handle_world)
            return int(handle)
        except Exception as exc:
            self.logger.debug("[IK] %s IK target 创建失败: %s", self.leaf, exc)
            return -1

    def _describe_joints(self) -> list[dict]:
        """采集每个本体关节的别名/父对象/模式 — §9.3 诊断要求。

        句柄定位出错时很难从"6 个关节"这一行看出来, 故逐轴记录。
        """
        sim = self.sim
        info = []
        for jh, mode in zip(self.joints, self.joint_modes):
            alias, parent_alias = "", ""
            try:
                alias = sim.getObjectAlias(jh)
            except Exception:
                pass
            try:
                parent = sim.getObjectParent(jh)
                if parent >= 0:
                    parent_alias = sim.getObjectAlias(parent)
            except Exception:
                pass
            info.append({"handle": jh, "alias": alias,
                         "parent_alias": parent_alias, "mode": mode})
        return info

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
                    if sim.getObjectAlias(obj) not in (
                            getattr(cfg, "ARM_SUCTION_ALIAS", "Suction"),
                            cfg.ARM_GRIPPER_ALIAS):
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
                if self.sim.getObjectAlias(obj) in (
                        getattr(cfg, "ARM_SUCTION_ALIAS", "Suction"),
                        cfg.ARM_GRIPPER_ALIAS):
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

    def _count_dynamic_shapes(self) -> int:
        """子树内仍是 static=0 的 shape 数 — 纯运动学改造是否已完成。

        kinematic 的前置安全网: 只要还有动态 shape, 切到 kinematic 就会让它们
        失去约束而自由落体, 且关节角读数看不出来 (见 CLAUDE.md 坑 9)。
        读不到时 (API 不可用 / 测试夹具未实现) 返回 0 即不阻断 —— 这是安全网,
        不是模式切换的必要条件。
        """
        count = 0
        for h in self._subtree():
            try:
                if (self.sim.getObjectType(h) == self.sim.object_shape_type
                        and self.sim.getObjectInt32Param(
                            h, self.sim.shapeintparam_static) == 0):
                    count += 1
            except Exception:
                return 0
        return count

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
        if mode == "kinematic":
            still_dynamic = self._count_dynamic_shapes()
            if still_dynamic:
                self.logger.error(
                    f"[ROBOT] {self.leaf} 拒绝切换到 kinematic: 子树内仍有 "
                    f"{still_dynamic} 个动态 shape。kinematic 关节不会为它们建立"
                    "约束, 机械臂会自由落体 —— 而关节角读数仍等于设定值, 事后"
                    "查不出来。请用当前 build_scene 重建场景 "
                    "(make_arm_kinematic 会把整条子树设为非动态)。"
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
        """下发单关节目标位置 — Command a single joint (阶段一单关节验证用).

        其余五轴沿用它们当前的目标位。目标位读不回来时**拒绝下发** ——
        否则会拿 0.0 补齐, 把其余五轴写到零位。
        """
        if not self.enabled or not 0 <= index < len(self.joints):
            return False
        angles = self.read_targets()
        if angles is None:
            self.logger.warning(
                f"[ROBOT] {self.leaf} 目标位读取失败, 拒绝单关节下发 "
                "(避免把其余五轴写成 0.0)"
            )
            return False
        while len(angles) < len(self.joints):
            angles.append(0.0)
        angles[index] = angle
        return self.command(angles, motion_params)

    def read_positions(self) -> list[float] | None:
        """读回六轴实际位置 (rad) — Read actual joint positions.

        Returns:
            六个实际位置; **任一关节读取失败返回 None**。此处刻意不用 0.0
            兜底: 那会让 max_error() 在什么都没读到时算出 0.0, 把 §9.4 的
            "max|current - target| < tolerance" 判据变成谎报到位。
        """
        positions = []
        for jh in self.joints:
            try:
                positions.append(float(self.sim.getJointPosition(jh)))
            except Exception:
                return None
        return positions

    def read_targets(self) -> list[float] | None:
        """读回六轴目标位置 (rad) — Read commanded joint targets.

        Returns:
            六个目标位置; 任一关节读取失败返回 None (同 read_positions)。
        """
        targets = []
        for jh in self.joints:
            try:
                targets.append(float(self.sim.getJointTargetPosition(jh)))
            except Exception:
                return None
        return targets

    def max_error(self) -> float:
        """六轴实际位置与目标位置的最大偏差 (rad) — Max joint error.

        §9.4 的到位判据 (max|current - target| < tolerance) 直接用它。
        反馈读不到时返回 inf, 即判"未到位" —— 绝不能在什么都没读到的情况下
        返回 0.0 把动作判成已完成。
        """
        current = self.read_positions()
        target = self.read_targets()
        if current is None or target is None:
            return float("inf")
        return max(
            (abs(c - t) for c, t in zip(current, target)),
            default=0.0,
        )

    # ═══════════════════════════════════════════════════════════
    # 持久化 IK 上下文 (方案 §7)
    # ═══════════════════════════════════════════════════════════

    def ensure_ik_context(self) -> bool:
        """建立并**复用**这台臂的 RobotArmIKContext — Build IK context once.

        方案 §7: "每台机械臂建立并复用一个 RobotArmIKContext，禁止在每个
        控制周期重新创建 IK 环境"。本方法幂等: 已有上下文直接返回 True。

        Returns:
            True 表示 IK 上下文可用; False 表示 simIK 缺失/环境不可建
            (此时 `solve_ik` 一律返回失败, **不会**退回关键帧假装到位)。
        """
        if self.ik_context is not None:
            return True
        ik = self.sim_ik
        if ik is None or self.tcp_handle < 0 or not self.joints:
            return False
        try:
            env = ik.createEnvironment()
            group_main = ik.createGroup(env)
            group_fallback = ik.createGroup(env)
            # ⚠ 实测 (2026-10-13): 带姿态约束 (constraint_pose) 的 IK 在取料点
            # 常常无解 —— handleGroup 返回 result_fail, 角误差已满足但线误差
            # 0.14 m。故这里用**位置约束**求解, 再由 has_reached_pick() 用
            # ARM_TOOL_MAX_TILT 硬性挡住"吸盘翻转"的抓取 (方案 §7 的意图用
            # 可验证的判据落地, 而不是靠一个解不出来的约束)。
            constraint = getattr(ik, "constraint_position", 7)
            element = -1
            for group in (group_main, group_fallback):
                added = ik.addElementFromScene(
                    env, group, self.root_handle, self.tcp_handle,
                    self._ik_target_handle, constraint)
                # 4.10 返回 (resultCode, {objHandle: ikElementHandle}, 反向映射)
                if isinstance(added, (list, tuple)) and len(added) >= 2 \
                        and isinstance(added[1], dict):
                    if added[0] not in (0, None):
                        self.logger.warning(
                            "[IK] %s addElementFromScene 返回 %s",
                            self.leaf, added[0])
                    element = int(added[1].get(self.tcp_handle, -1))
                elif not added:
                    self.logger.warning(
                        "[IK] %s addElementFromScene 失败", self.leaf)
                    return False
            # 主组: 伪逆; 备用组: 阻尼最小二乘 (方案 §7)
            ik.setGroupCalculation(env, group_main,
                                   ik.method_pseudo_inverse, 0.0,
                                   cfg.ARM_IK_MAX_ITERATIONS)
            ik.setGroupCalculation(env, group_fallback,
                                   ik.method_damped_least_squares, 0.1,
                                   cfg.ARM_IK_MAX_ITERATIONS)
            limits = []
            for joint in self.joints:
                try:
                    info = self.sim.getJointInterval(joint)
                    # 4.10 返回 [min, max, cyclic] 或 [(min,max), cyclic]
                    if isinstance(info[0], (list, tuple)):
                        limits.append((float(info[0][0]), float(info[0][1])))
                    else:
                        limits.append((float(info[0]), float(info[1])))
                except Exception:
                    limits.append((-2.0 * math.pi, 2.0 * math.pi))
            self.ik_context = {
                "env": env,
                "group_main": group_main,
                "group_fallback": group_fallback,
                "target": self._ik_target_handle,
                "element": element,
                "joints": list(self.joints),
                "limits": limits,
                "failures": 0,
                "orientation_relaxed": False,
            }
            self.logger.info(
                "[IK] %s 上下文已建立 (joints=%d)", self.leaf, len(self.joints))
            return True
        except Exception as exc:
            self.logger.warning("[IK] %s 上下文建立失败: %s", self.leaf, exc)
            return False

    def solve_ik(self, target_position: list[float],
                 target_orientation: list[float] | None = None) -> dict:
        """把 TCP 解算到目标位姿 — Solve TCP pose, no silent fallback.

        方案 §7: 用持久化的主组 (伪逆) 求解, 失败时切备用组 (阻尼最小二乘);
        两组都失败 → 返回 `{"ok": False}`, **不**退回关键帧姿态。

        Args:
            target_position:     世界坐标目标 [x, y, z]。
            target_orientation:  世界姿态欧拉角; None 表示只约束位置。

        Returns:
            {"ok": bool, "group": "main"|"fallback"|None,
             "reason": str, "precision": float|None,
             "joints": list[float]|None}
        """
        result = {"ok": False, "group": None, "reason": "", "precision": None,
                  "joints": None}
        if not self.enabled:
            result["reason"] = "device disabled"
            return result
        if not self.ensure_ik_context():
            result["reason"] = "IK 上下文不可用 (simIK 缺失?)"
            return result
        ctx = self.ik_context
        sim = self.sim
        ik = self.sim_ik
        try:
            sim.setObjectPosition(ctx["target"], list(target_position[:3]),
                                  sim.handle_world)
            if target_orientation is not None:
                sim.setObjectOrientation(ctx["target"], list(target_orientation),
                                         sim.handle_world)
        except Exception as exc:
            result["reason"] = f"设置 IK target 失败: {exc}"
            return result

        # 主组 → 备用组 (方案 §7), 两组都失败时再放宽姿态约束重试一次。
        # ⚠ 实测 (2026-10-13): 保持"吸盘朝下"的**姿态约束**在取料点常常解不出
        # (handleGroup 返回 result_fail, 角误差已满足但线误差 0.14 m) —— 手臂
        # 从 HOME 姿态平移下探时可达位姿集很窄。故: 姿态优先, 解不出就退化为
        # **位置约束**, 并如实记录 `orientation_relaxed=True` (不是假装成功)。
        # ⚠ 必须成对使用 syncFromSim / syncToSim (官方手册 simIK.htm):
        #   `addElementFromScene` 会把场景状态**快照**进 IK 世界, 之后场景变了
        #   IK 世界并不会自己更新。早期版本只调 handleGroup(..., syncWorlds),
        #   结果 IK 世界里的 target 永远停在"环境创建时 TCP 所在的位置" ——
        #   目标与当前位姿恒等, handleGroup 每次都"成功"且精度 ~1e-4, 而机械臂
        #   一动不动 (实测 400 步 TCP 坐标完全不变, ik_index 卡在 0)。
        #   正确顺序: 写场景 target → syncFromSim → handleGroup → syncToSim。
        attempts = [("group_main", None), ("group_fallback", None)]
        for name, override in attempts:
            group = ctx[name]
            try:
                ik.syncFromSim(ctx["env"], [group])
            except Exception as exc:
                result["reason"] = f"{name} syncFromSim 异常: {exc}"
                continue
            try:
                out = ik.handleGroup(ctx["env"], group, {"syncWorlds": True})
            except Exception as exc:
                result["reason"] = f"{name} handleGroup 异常: {exc}"
                continue
            res = out[0] if isinstance(out, (list, tuple)) else out
            reason = out[1] if isinstance(out, (list, tuple)) and len(out) > 1 else ""
            precision = out[2] if isinstance(out, (list, tuple)) and len(out) > 2 else None
            if res == ik.result_success:
                # 把解写回场景 (syncWorlds 选项在 ZMQ 路径下不生效, 用显式 syncToSim)
                try:
                    ik.syncToSim(ctx["env"], [group])
                except Exception as exc:
                    result["reason"] = f"{name} syncToSim 异常: {exc}"
                    continue
                joints = self._read_synced_joints(ctx)
                joints = self._clamp_to_limits(joints, ctx["limits"])
                # ⚠ 这里**不能**再 command(joints)。syncToSim 已经把关节位置直接
                # 写进场景; 再调 setJointTargetPosition 会让 kinematic 关节按运动
                # 轮廓重新逼近同一个目标, 与刚写入的位置互相打架, 实测表现为
                # TCP 第一步之后就不再跟随 IK 目标 (第 2 步起坐标完全冻结)。
                result.update({"ok": True, "group": name, "reason": reason,
                               "precision": precision, "joints": joints})
                ctx["failures"] = 0
                return result
            result["reason"] = f"{name}: {reason or res}"
        ctx["failures"] = ctx.get("failures", 0) + 1
        # 只在前几次和每 100 次告警, 避免逐帧刷屏 (调用方仍拿到 ok=False)
        if ctx["failures"] <= 2 or ctx["failures"] % 100 == 0:
            self.logger.warning(
                "[IK] %s 主组与阻尼组均失败 (%s) — 动作判为 FAILED, "
                "不用关键帧姿态冒充到位 (累计失败 %d 次)",
                self.leaf, result["reason"], ctx["failures"])
        return result

    def _read_synced_joints(self, ctx: dict) -> list[float]:
        """读取 handleGroup(syncWorlds=True) 同步回场景的关节角。

        ⚠ 4.10 实测 (2026-10-13): `simIK.getJointPosition(env, sceneJointHandle)`
        会抛 `Invalid joint handle: <n>` —— 该函数只接受 IK 环境内部的关节
        句柄。既然用了 `syncWorlds`，正确做法是**直接读场景关节**
        (`sim.getJointPosition`)，它已被同步。IK 环境读取作为兜底保留。
        """
        joints = []
        for j in ctx["joints"]:
            try:
                joints.append(float(self.sim.getJointPosition(j)))
            except Exception:
                try:
                    joints.append(float(self.sim_ik.getJointPosition(
                        ctx["env"], j)))
                except Exception:
                    joints.append(0.0)
        return joints

    @staticmethod
    def _clamp_to_limits(joints: list[float],
                         limits: list[tuple[float, float]]) -> list[float]:
        """关节限位同步 — Clamp IK solution into joint limits (方案 §7)。"""
        out = []
        for value, (lo, hi) in zip(joints, limits):
            if hi > lo:
                out.append(min(max(value, lo), hi))
            else:
                out.append(value)
        return out


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

    def __init__(self, sim, logger: logging.Logger, sim_ik=None):
        """初始化动画器并采集各机械臂关节句柄。

        Args:
            sim:    CoppeliaSim Remote API "sim" 模块 (已连接).
            logger: 日志记录器.
        """
        self.sim = sim
        self.sim_ik = sim_ik
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
            device = RobotArmDevice(self.sim, key, robot_cfg, self.logger,
                                    sim_ik=self.sim_ik)
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
                "ik_start": None,
                "ik_goal": None,
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

    def trigger(self, island_key: str, anim_name: str,
                target_position: list[float] | None = None):
        """触发某机械臂播放指定动画 — Trigger an animation.

        Args:
            island_key: cfg.ISLAND_ROBOTS 的键 (如 "A", "E_C").
            anim_name:  cfg.ARM_ANIMATIONS 的键 (如 "pick_and_place").
            target_position: 若给出, 该动作以 IK 目标驱动而不是关键帧插值。
        """
        arm = self.arms.get(island_key)
        if arm is None or arm["n_joints"] == 0:
            return
        if anim_name not in cfg.ARM_ANIMATIONS:
            return
        arm["anim"] = anim_name
        arm["frame"] = 0
        arm["frame_t"] = 0.0
        arm["ik_start"] = arm["ik_goal"] = None
        arm["ik_waypoints"] = None
        arm["ik_index"] = 0
        arm["ik_failed"] = False
        self._capture_tool_orientation(arm)
        if target_position is not None:
            arm["ik_goal"] = list(target_position[:3])
        else:
            get_pos = getattr(self.sim, "getObjectPosition", None)
            if callable(get_pos) and arm["device"].tcp_handle >= 0:
                try:
                    frame = getattr(self.sim, "handle_world", -1)
                    start = list(get_pos(arm["device"].tcp_handle, frame))[:3]
                    # 吸附动作上下探；放置动作向工作台中心侧移。
                    dz = -0.16 if anim_name == "pick_and_place" else -0.10
                    dx = 0.12 if anim_name in ("pick_and_place", "assemble") else 0.0
                    arm["ik_start"] = start
                    arm["ik_goal"] = [start[0] + dx, start[1], start[2] + dz]
                except Exception:
                    pass

    def _capture_tool_orientation(self, arm: dict):
        """记录当前 TCP 世界姿态, 全套动作保持不变 — Keep suction pointing down.

        方案 §7: "目标朝向固定为吸盘朝下, 避免吸盘在抓取时翻转"。这里不做
        姿态标定猜测, 而是**保持动作开始时的 TCP 世界姿态不变** —— 只要起始
        姿态的吸盘是朝下的 (HOME 姿态如此), 整个取放过程吸盘就不会翻转。
        """
        device = arm["device"]
        if device.tcp_handle < 0:
            arm["ik_orient"] = None
            return
        try:
            orient = list(self.sim.getObjectOrientation(
                device.tcp_handle, getattr(self.sim, "handle_world", -1)))
            arm["ik_orient"] = orient
            # 同时记下工具 Z 轴, 用于"吸盘是否翻转"的硬判据
            arm["tool_axis_ref"] = euler_to_axis(orient)
        except Exception:
            arm["ik_orient"] = None
            arm["tool_axis_ref"] = None

    def start_pick_and_place(self, island_key: str,
                             pick_position: list[float],
                             place_position: list[float],
                             approach_height: float = None) -> bool:
        """以 IK 路径点驱动一次取放 — Drive a real IK pick-and-place.

        路径点顺序 (方案 §6.3 / §7):
            1. 抓取点上方 (approach)   2. 抓取点 (attach 在此确认)
            3. 抬起回 approach         4. 放置点上方 (approach)
            5. 放置点 (release)

        Args:
            island_key:      机械臂键 (cfg.ISLAND_ROBOTS).
            pick_position:   物料当前世界坐标 (抓取点)。
            place_position:  目标台面上的放置点世界坐标。
            approach_height: 抬升高度 (m), 默认 cfg.ARM_PICK_APPROACH_Z。

        Returns:
            True 表示已开始动作; False 表示该臂不可用。
        """
        arm = self.arms.get(island_key)
        if arm is None or arm["n_joints"] == 0:
            return False
        if arm["device"].tcp_handle < 0:
            return False
        h = cfg.ARM_PICK_APPROACH_Z if approach_height is None else approach_height
        pick = list(pick_position[:3])
        place = list(place_position[:3])
        arm["anim"] = "pick_and_place"
        arm["frame"] = 0
        arm["frame_t"] = 0.0
        arm["ik_goal"] = None
        arm["ik_start"] = None
        arm["ik_failed"] = False
        arm["ik_index"] = 0
        arm["ik_attempts"] = 0
        arm["ik_waypoints"] = [
            [pick[0], pick[1], pick[2] + h],    # 1 抓取点上方
            [pick[0], pick[1], pick[2]],        # 2 抓取点
            [pick[0], pick[1], pick[2] + h],    # 3 抬起
            [place[0], place[1], place[2] + h],  # 4 放置点上方
            [place[0], place[1], place[2]],     # 5 放置点
        ]
        arm["pick_position"] = pick
        arm["place_position"] = place
        self._capture_tool_orientation(arm)
        self.logger.info(
            "[IK] %s 取放 path: pick=%s place=%s (5 路径点)",
            arm["device"].leaf,
            [round(v, 3) for v in pick], [round(v, 3) for v in place])
        return True

    def tool_tilt_now(self, island_key: str):
        """当前末端工具轴相对动作起始姿态的偏转角 (rad) — Tool tilt.

        方案 §7 要求"目标朝向固定为吸盘朝下, 避免吸盘在抓取时翻转"。IK 用
        位置约束求解时姿态不受约束, 故用本判据在抓取时刻硬性拦截翻转。
        返回 None 表示无法测量 (调用方不应据此判定失败)。
        """
        arm = self.arms.get(island_key)
        if arm is None:
            return None
        ref = arm.get("tool_axis_ref")
        device = arm["device"]
        if ref is None or device.tcp_handle < 0:
            return None
        try:
            orient = self.sim.getObjectOrientation(
                device.tcp_handle, getattr(self.sim, "handle_world", -1))
        except Exception:
            return None
        return axis_tilt(ref, euler_to_axis(orient))

    def tool_is_upright(self, island_key: str) -> bool:
        """末端工具是否未翻转 (倾角在 cfg.ARM_TOOL_MAX_TILT 内)。"""
        tilt = self.tool_tilt_now(island_key)
        return True if tilt is None else tilt <= cfg.ARM_TOOL_MAX_TILT

    def has_reached_pick(self, island_key: str) -> bool:
        """机械臂是否已下探到抓取点 (第 2 个路径点) — For attach gating."""
        arm = self.arms.get(island_key)
        if arm is None or arm.get("ik_waypoints") is None:
            return False
        if arm.get("ik_index", 0) < 2:
            return False
        # 姿态门: 吸盘翻转过就不许抓取 (方案 §7)
        if not self.tool_is_upright(island_key):
            if not arm.get("tilt_blocked"):
                arm["tilt_blocked"] = True
                self.logger.warning(
                    "[SUCTION] %s 末端工具倾角 %.3f rad 超过 %.3f — 拒绝抓取 "
                    "(方案 §7 防翻转)", arm["device"].leaf,
                    self.tool_tilt_now(island_key) or 0.0, cfg.ARM_TOOL_MAX_TILT)
            return False
        return True

    def is_action_done(self, island_key: str) -> bool:
        """机械臂动作是否已走完全部路径点 — For release gating."""
        arm = self.arms.get(island_key)
        if arm is None:
            return True
        return arm.get("anim") is None


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

        # ── IK 路径点动作优先 ──
        # ⚠ 实测 bug (2026-10-13): 关键帧时序会**先**跑完 (8 帧 × 0.6 s ≈ 84 步)
        # 并把 arm["anim"] 清掉, 于是一次 IK 取放还没走到第一个路径点就被腰斩
        # (实测 ik_index 停在 0, TCP 一动不动)。有 ik_waypoints 时完成判据必须
        # 换成"路径点走完", 关键帧时序整段让位。
        if arm.get("ik_waypoints"):
            self._step_ik_waypoints(arm)
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

        if arm.get("ik_goal") is not None:
            start = arm.get("ik_start") or arm["ik_goal"]
            target = [start[i] + (arm["ik_goal"][i] - start[i]) * t
                      for i in range(3)]
            result = arm["device"].solve_ik(target, arm.get("ik_orient"))
            if result["ok"]:
                arm["ik_failed"] = False
            elif not arm.get("ik_failed"):
                arm["ik_failed"] = True
                arm["ik_fail_reason"] = result.get("reason", "")
                self.logger.warning(
                    "[IK] %s 目标不可达 (%s) — 动作 FAILED, 保持上一姿态",
                    arm["device"].leaf, result.get("reason", ""))
            return

    def _step_ik_waypoints(self, arm: dict):
        """推进一次 IK 取放的一个路径点 — Drive one IK waypoint.

        完成判据 (方案 §7):
          - 用 **TCP 世界坐标**判到位, 不用关节角 (CLAUDE.md 坑 9: kinematic
            模式下关节角可能精确等于设定值而连杆已塌陷, 只查角会假通过)
          - 走完最后一个路径点才清 `anim`, 由 `is_action_done` 对外表达完成
          - 某个路径点连续 `ARM_IK_MAX_ATTEMPTS` 次解不出来 → 动作判 FAILED
            并结束, 由控制器退避重试 (不是永远卡住, 也不是假装成功)
        """
        waypoints = arm.get("ik_waypoints") or []
        index = arm.get("ik_index", 0)
        if index >= len(waypoints):
            arm["anim"] = None
            return
        target = waypoints[index]
        # ── 目标按每步限幅逼近 (关键) ──
        # ⚠ 实测: 把 IK target 一次跳到 0.7 m 外, `handleGroup` 用伪逆/阻尼最小
        # 二乘都解不出来 (返回 result_fail, 线误差 0.47 m) —— 这是一步到位的
        # 数值 IK 的固有困难。改成每个控制步只把 target 沿直线推进
        # ARM_IK_TARGET_STEP 米, 每步只解一个小位移, 收敛稳定 (标准做法)。
        step_target = self._limited_ik_target(arm, target)
        result = arm["device"].solve_ik(step_target, arm.get("ik_orient"))
        if result["ok"]:
            arm["ik_failed"] = False
            arm["ik_attempts"] = 0
            if self._tcp_near(arm, target):
                arm["ik_index"] = index + 1
                arm["ik_attempts"] = 0
                self.logger.debug(
                    "[IK] %s 路径点 %d/%d 到位",
                    arm["device"].leaf, index + 1, len(waypoints))
            return
        arm["ik_attempts"] = arm.get("ik_attempts", 0) + 1
        if not arm.get("ik_failed"):
            arm["ik_failed"] = True
            arm["ik_fail_reason"] = result.get("reason", "")
            self.logger.warning(
                "[IK] %s 路径点 %d 不可达 (%s) — 动作 FAILED, 保持上一姿态",
                arm["device"].leaf, index, result.get("reason", ""))
        if arm["ik_attempts"] >= cfg.ARM_IK_MAX_ATTEMPTS:
            arm["anim"] = None
            self.logger.error(
                "[IK] %s 路径点 %d 连续 %d 次不可达 — 放弃本次取放 (控制器将退避重试)",
                arm["device"].leaf, index, arm["ik_attempts"])

    def _limited_ik_target(self, arm: dict, goal: list[float]) -> list[float]:
        """把本步的 IK 目标限制在当前 TCP 附近 — Rate-limited IK target.

        每次只朝最终路径点推进 `cfg.ARM_IK_TARGET_STEP` 米, 让每步的 IK 都是
        一个小位移问题。测不出 TCP 时直接返回 goal (退化为一次到位)。
        """
        device = arm["device"]
        if device.tcp_handle < 0:
            return list(goal[:3])
        try:
            current = list(self.sim.getObjectPosition(
                device.tcp_handle, getattr(self.sim, "handle_world", -1)))[:3]
        except Exception:
            return list(goal[:3])
        delta = [goal[i] - current[i] for i in range(3)]
        distance = math.dist(current, list(goal[:3]))
        limit = cfg.ARM_IK_TARGET_STEP
        if distance <= limit or distance < 1e-9:
            return list(goal[:3])
        scale = limit / distance
        return [current[i] + delta[i] * scale for i in range(3)]

    def arm_action_failed(self, island_key: str) -> bool:
        """该臂当前动作是否已判定失败并结束 — For controller retry gating."""
        arm = self.arms.get(island_key)
        if arm is None:
            return False
        return bool(arm.get("ik_failed")) and arm.get("anim") is None

    def _tcp_near(self, arm: dict, target: list[float],
                  tolerance: float = None) -> bool:
        """TCP 世界坐标是否已到达目标 — World-space arrival test.

        ⚠ 必须查世界坐标而不是关节角: kinematic 模式下关节角读数可能精确等于
        设定值而连杆已经塌陷 (CLAUDE.md 坑 9), 只查关节角会得到假通过。
        """
        device = arm["device"]
        if device.tcp_handle < 0:
            return True
        tolerance = cfg.ARM_POSITION_TOLERANCE * 4.0 if tolerance is None \
            else tolerance
        try:
            actual = self.sim.getObjectPosition(
                device.tcp_handle, getattr(self.sim, "handle_world", -1))
        except Exception:
            return True
        return math.dist(actual[:3], list(target[:3])) <= tolerance

    def ik_failure_count(self) -> int:
        """统计处于 IK 失败状态的机械臂数 — For verification / status."""
        return sum(1 for a in self.arms.values() if a.get("ik_failed"))

    def _command_pose(self, arm: dict, angles: list[float]):
        """下发整组关节目标 — Delegates to the device layer."""
        arm["device"].command(angles)

    # ═══════════════════════════════════════════════════════════
    # 吸盘吸附 / 释放 (方案 §6.3) — 内置脚本无公开函数时的兼容回退
    # ═══════════════════════════════════════════════════════════

    def _suction_hits(self, arm: dict, material) -> bool:
        """吸盘接近传感器是否命中该物料 — Silent proximity check.

        用 `sim.checkProximitySensor(sensor, shape)` 而不是 `readProximitySensor`:
        后者会检查 detectable 标志, 而多数 respondable 形状默认不可探测
        (见吸盘模型脚本注释与 docs/wiki/02-仿真建模/02-07-接近传感器.md)。
        """
        device = arm["device"]
        sensor = getattr(device, "suction_sensor_handle", -1)
        if sensor < 0 or material.handle < 0:
            return False
        try:
            return self.sim.checkProximitySensor(sensor, material.handle) == 1
        except Exception:
            return False

    def attach_material(self, island_key: str, material) -> bool:
        """用吸盘把末端等待区的物料吸附到末端 — Suction attach (方案 §6.3).

        回退流程 (内置 suction 脚本没有公开 activate/deactivate 函数, 见
        cfg.SUCTION_USE_BUILTIN_SCRIPT 注释):
          1. 接近传感器静默检测命中该物料
          2. 校验物料状态: 只有 WAITING_FOR_SUCTION 的物料可被吸附, 防止吸
             传送带 pad / 挡停件 / 别人的物料 (方案 §6.3 第 2 条)
          3. `sim.setObjectParent(material, link, True)` 并记录相对位姿
             (方案 §6.3 第 3 条), 供移动期间校验 (第 4 条)
        """
        arm = self.arms.get(island_key)
        if arm is None or material.handle < 0:
            return False
        device = arm["device"]
        # 吸附参考点优先用吸盘模型的 Link (力传感器), 否则用 TCP
        link = device.suction_handle if device.suction_handle >= 0 else device.tcp_handle
        if link < 0:
            return False

        # 2) 状态门: 只吸附等待取料的物料
        attachable = getattr(cfg, "SUCTION_ATTACHABLE_STATES",
                             ("WAITING_FOR_SUCTION",))
        if getattr(material, "state", None) not in attachable:
            self.logger.warning(
                "[SUCTION] 拒绝吸附 mat#%s: 状态 %s 不在 %s (方案 §6.3 第 2 条)",
                material.uid, getattr(material, "state", None), attachable)
            return False

        # 1b) 姿态门: 翻转的吸盘不吸附 (方案 §7)
        if not self.tool_is_upright(island_key):
            self.logger.warning(
                "[SUCTION] 拒绝吸附 mat#%s: 末端工具倾角超限 (方案 §7)",
                material.uid)
            return False

        # 1) 传感器命中确认 — 连续 N 帧, 防单帧抖动
        if device.suction_sensor_handle >= 0:
            if not self._suction_hits(arm, material):
                return False
            arm["suction_hits"] = arm.get("suction_hits", 0) + 1
            if arm["suction_hits"] < cfg.SUCTION_CONFIRM_FRAMES:
                return False
        else:
            self.logger.warning(
                "[SUCTION] %s 未找到吸盘接近传感器, 退化为纯位姿吸附",
                device.leaf)

        try:
            sim = self.sim
            sim.setObjectParent(material.handle, link, True)
            # 记录 attach 时的相对位姿, 移动期间用它校验吸附是否失效
            pose = sim.getObjectPose(material.handle, link)
            material.suction_attached = True
            material.awaiting_pickup = True
            material.frozen = True
            arm["suction_hits"] = 0
            arm["attached_uid"] = material.uid
            arm["attach_pose"] = list(pose)
            return True
        except Exception as exc:
            self.logger.warning(
                "[SUCTION] 吸附 mat#%s 失败: %s", material.uid, exc)
            return False

    def verify_suction_hold(self, island_key: str, material) -> bool:
        """移动期间校验物料仍牢牢贴在吸盘上 (方案 §6.3 第 4 条)。

        相对位姿偏离超过 cfg.SUCTION_HOLD_TOLERANCE 即判定吸附失效, 调用方
        应把该物料动作转入 FAILED 而不是静默继续。
        """
        arm = self.arms.get(island_key)
        if arm is None or material.handle < 0:
            return False
        reference = arm.get("attach_pose")
        device = arm["device"]
        link = device.suction_handle if device.suction_handle >= 0 else device.tcp_handle
        if reference is None or link < 0:
            return True
        try:
            now = list(self.sim.getObjectPose(material.handle, link))
        except Exception:
            return False
        drift = max(abs(a - b) for a, b in zip(reference, now))
        if drift > cfg.SUCTION_HOLD_TOLERANCE:
            self.logger.warning(
                "[SUCTION] mat#%s 相对吸盘位姿漂移 %.4f m > %.3f — 吸附失效",
                material.uid, drift, cfg.SUCTION_HOLD_TOLERANCE)
            return False
        return True

    def release_material(self, island_key: str, material,
                         drop_position: list[float] | None = None) -> bool:
        """释放物料并把它放到目标台面 — Release + restore dynamics.

        方案 §6.3 第 5 条: 解除父子关系, 恢复 static/dynamic、respondable 和
        碰撞 mask, 并调用 `sim.resetDynamicObject` 后再继续仿真。
        """
        arm = self.arms.get(island_key)
        if material.handle < 0:
            return False
        sim = self.sim
        try:
            sim.setObjectParent(material.handle, -1, True)
            if drop_position is not None:
                sim.setObjectPosition(material.handle, list(drop_position),
                                      sim.handle_world)
            # 恢复动态属性 (mount 时并未改 static, 但吸附/冻结可能改过)
            from reference.factory_physics import apply_material_physics_props
            apply_material_physics_props(sim, material.handle, None)
            sim.resetDynamicObject(material.handle)
        except Exception as exc:
            self.logger.warning(
                "[SUCTION] 释放 mat#%s 失败: %s", material.uid, exc)
            return False
        material.suction_attached = False
        if arm is not None:
            arm["attached_uid"] = None
            arm["attach_pose"] = None
            arm["suction_hits"] = 0
        return True


def euler_to_axis(euler: list) -> list:
    """欧拉角 → 3×3 旋转矩阵的第三列 (工具 Z 轴方向) — Tool Z axis in world.

    不依赖 numpy: 直接按 CoppeliaSim 的 alpha-beta-gamma (X→Y→Z 外旋) 约定
    展开。用于比较"吸盘是否还朝下", 比直接比较欧拉角三元组可靠。
    """
    a, b, g = (list(euler) + [0.0, 0.0, 0.0])[:3]
    ca, sa = math.cos(a), math.sin(a)
    cb, sb = math.cos(b), math.sin(b)
    cg, sg = math.cos(g), math.sin(g)
    return [
        ca * cg * sb + sa * sg,     # R[0][2]
        -cg * sa + ca * sb * sg,    # R[1][2]
        ca * cb,                    # R[2][2]
    ]


def axis_tilt(ref_axis: list, axis: list) -> float:
    """两个方向向量之间的夹角 (rad) — Angle between two unit-ish vectors."""
    n1 = math.sqrt(sum(v * v for v in ref_axis))
    n2 = math.sqrt(sum(v * v for v in axis))
    if n1 < 1e-9 or n2 < 1e-9:
        return 0.0
    dot = sum(a * b for a, b in zip(ref_axis, axis)) / (n1 * n2)
    return math.acos(max(-1.0, min(1.0, dot)))
