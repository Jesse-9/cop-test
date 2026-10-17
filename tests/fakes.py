"""tests/fakes.py — 测试用共享 fake (test-only utilities)

FakeSuctionSim: 完整模拟 SuctionController 需要的 sim 读写下, 使吸附/释放
逻辑可以在无 CoppeliaSim 环境下端到端跑通 (附加 tests/test_suction_controller.py
与 tests/test_stage1_unit_loop.py 共用, 避免各写一份漂移)。
"""


class FakeSuctionSim:
    """SuctionController 的最小可用 sim 桩。"""

    def __init__(self):
        self.calls = []
        self.parent_of = {}       # handle → parent
        self.static = {}          # handle → int
        self.respondable = {}
        self.mass = {}
        self.pose = {}
        self.vel = {}
        self.sensor_hit = None    # checkProximitySensor 命中句柄

    # ── 属性读写 ──
    def getObjectParent(self, h):
        self.calls.append(("getObjectParent", h))
        return self.parent_of.get(h, -1)

    def setObjectParent(self, h, parent, keep):
        self.calls.append(("setObjectParent", h, parent, keep))
        self.parent_of[h] = parent

    def getObjectPose(self, h):
        self.calls.append(("getObjectPose", h))
        return self.pose.get(h, [0, 0, 0, 0, 0, 0, 1])

    def getObjectPosition(self, h, rel=None):
        self.calls.append(("getObjectPosition", h))
        return list(self.pose.get(h, [0, 0, 0, 0, 0, 0, 1])[:3])

    def getObjectQuaternion(self, h, rel=None):
        self.calls.append(("getObjectQuaternion", h))
        return list(self.pose.get(h, [0, 0, 0, 0, 0, 0, 1])[3:7])

    def setObjectPose(self, h, pose, rel):
        self.calls.append(("setObjectPose", h, tuple(pose), rel))
        self.pose[h] = list(pose)

    def setObjectPosition(self, h, pos, rel):
        self.calls.append(("setObjectPosition", h, tuple(pos), rel))
        self.pose.setdefault(h, [0, 0, 0, 0, 0, 0, 1])[:3] = list(pos)

    def setObjectQuaternion(self, h, q, rel):
        self.calls.append(("setObjectQuaternion", h, tuple(q), rel))

    # 参数名可能是 sim.shapeintparam_static 等常量对象, 统一做子串匹配
    def getObjectInt32Parameter(self, h, p):
        self.calls.append(("getObjectInt32Parameter", h, p))
        name = str(p)
        if "static" in name:
            return self.static.get(h, 0)
        if "respondable" in name:
            return self.respondable.get(h, 1)
        return 0

    def setObjectInt32Param(self, h, p, v):
        self.calls.append(("setObjectInt32Param", h, p, v))
        name = str(p)
        if "static" in name:
            self.static[h] = v
        elif "respondable" in name:
            self.respondable[h] = v

    def getObjectFloatParameter(self, h, p):
        self.calls.append(("getObjectFloatParameter", h, p))
        return self.mass.get(h, 1.0)

    def setObjectFloatParam(self, h, p, v):
        self.calls.append(("setObjectFloatParam", h, p, v))
        self.mass[h] = v

    def setFloatProperty(self, h, name, v):
        self.calls.append(("setFloatProperty", h, name, v))

    def setBoolProperty(self, h, name, v):
        self.calls.append(("setBoolProperty", h, name, v))
        if name == "respondable":
            self.respondable[h] = 1 if v else 0

    def setIntProperty(self, h, name, v):
        self.calls.append(("setIntProperty", h, name, v))

    def getObjectVelocity(self, h):
        self.calls.append(("getObjectVelocity", h))
        return self.vel.get(h, [0.0, 0.0, 0.0])

    def resetDynamicObject(self, h):
        self.calls.append(("resetDynamicObject", h))

    def computeMassAndInertia(self, h, density):
        self.calls.append(("computeMassAndInertia", h, density))

    def checkProximitySensor(self, sensor, other):
        self.calls.append(("checkProximitySensor", sensor))
        if self.sensor_hit is None:
            return (0, None, 0.0)
        return (1, self.sensor_hit, 0.0, [0, 0, 0], [0, 0, 0])

    def __getattr__(self, name):
        # sim 常量兜底: sim.shapeintparam_static 等返回可子串匹配的字符串
        if name.startswith(("handle_", "simIK", "jointmode", "shapeintparam",
                            "objfloatparam", "object_", "primitiveshape")):
            class _Const:
                def __init__(self, n):
                    self._n = n

                def __getattr__(self, k):
                    return f"{self._n}.{k}"

                def __call__(self, *a):
                    return f"{self._n}{a}"

            return _Const(name)
        raise AttributeError(name)

    # ── 便捷断言辅助 ──
    def called(self, name):
        return [c for c in self.calls if c[0] == name]


def make_downward_tcp_sim(material_handle=99, tcp_handle=51,
                          material_pose=(0.5, 0.0, 0.9),
                          tcp_gap=0.0, parent=-1):
    """构造一个"吸盘朝下、物料在 TCP 正下方"的 FakeSuctionSim。

    quaternion (1,0,0,0) = 绕 X 转 180° → TCP 的 Z 轴朝世界 -Z (吸盘朝下),
    倾角检查通过。
    """
    sim = FakeSuctionSim()
    sim.pose[material_handle] = [material_pose[0], material_pose[1],
                                 material_pose[2], 0, 0, 0, 1]
    sim.pose[tcp_handle] = [material_pose[0], material_pose[1],
                            material_pose[2] + tcp_gap, 1, 0, 0, 0]
    sim.parent_of[material_handle] = parent
    sim.sensor_hit = material_handle
    return sim