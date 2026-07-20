# MCP 读取/诊断/控制工具扩展 — 实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在 [factory_mcp_server.py](../../../src/factory_mcp_server.py) 新增 10 个 MCP 工具(A 档 4 读取 + B 档 3 诊断 + C 档 3 控制),并加一个只读 ZMQ 连接解决单连接假断连。

**Architecture:** FactoryMCPServer 增加第二个 `CoppeliaConnector`(ro_connector)给所有只读诊断工具用,与主连接隔离;run_lua 用"创建临时定制脚本 + 信号回读"机制(已实测可行,见 spec);C 档 pause/resume/step_n 走主连接修改 controller。

**Tech Stack:** Python 3, FastMCP (mcp 包), coppeliasim-zmqremoteapi-client, pytest。

## Global Constraints

- Python 解释器固定: `e:\Work\paper\cop-test\.venv\Scripts\python.exe`。
- 命名贴 CLAUDE.md「已知问题」第 1 条:对象别名本身不含 `/`;查询时 `sim.getObject` 路径须以 `/` 开头。
- 所有新工具沿用现有非抛异常风格:失败返回 `"失败: <原因>"` 字符串,写 `server.last_error`,不抛(见 [factory_mcp_server.py:324](../../../src/factory_mcp_server.py#L324) 现有 `connect` 风格)。
- JSON 输出 `ensure_ascii=False`(沿用现有 `status`/`get_config`)。
- 测试需连接真实 CoppeliaSim(集成测试),无 CoppeliaSim 时测试自动 skip;纯逻辑(状态码映射、护栏审计)用单测。
- run_lua 关键约束(实测):`load`/`loadstring` 在定制脚本沙箱中被禁用,用户代码必须**嵌入脚本源码**而非运行时编译。

---

## File Structure

| 文件 | 责任 | 新建/修改 |
|------|------|-----------|
| `src/factory_mcp_server.py` | 加 ro_connector + 10 个工具 + run_lua 护栏 | 修改 |
| `src/factory_controller.py` | 加 `_pause_requested` 标志 + 主循环检测点 | 修改 |
| `tests/conftest.py` | CoppeliaSim 连接 fixture + skip 钩子 | 新建 |
| `tests/test_mcp_read_tools.py` | A 档工具集成测试 | 新建 |
| `tests/test_mcp_diagnose_tools.py` | B 档工具集成测试(含 run_lua 护栏单测+集成) | 新建 |
| `tests/test_mcp_control_tools.py` | C 档工具集成测试 | 新建 |
| `tests/test_state_codes.py` | 状态码映射纯单测(无需 CoppeliaSim) | 新建 |
| `docs/MCP_USAGE.md` | 文档更新(20 个工具) | 修改 |

---

## Task 1: 测试基建 — conftest 与 CoppeliaSim 连接 fixture

**Files:**
- Create: `tests/conftest.py`

**Interfaces:**
- Produces: `pytest` fixture `mcp_server` 返回已连接的 `FactoryMCPServer` 实例(连接失败则 skip);`coppeliasim_available` 用于条件 skip。

- [ ] **Step 1: 写 conftest.py**

```python
# tests/conftest.py
"""MCP 工具测试共享 fixture — shared fixtures for MCP tool tests."""
import sys
from pathlib import Path

# 让 tests/ 能 import src/factory_*
_SRC = Path(__file__).resolve().parent.parent / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

import pytest
from factory_mcp_server import FactoryMCPServer


def _coppeliasim_up(host: str = "localhost", port: int = 23000) -> bool:
    """探测 CoppeliaSim ZMQ 是否在线 — 不抛,只返回 bool。"""
    try:
        from coppeliasim_zmqremoteapi_client import RemoteAPIClient
        c = RemoteAPIClient(host=host, port=port)
        sim = c.require("sim")
        sim.getSimulationState()  # 探针
        return True
    except Exception:
        return False


@pytest.fixture(scope="session")
def coppeliasim_available():
    return _coppeliasim_up()


@pytest.fixture(scope="session")
def mcp_server(coppeliasim_available):
    """已连接的 FactoryMCPServer,断开在 session 结束。"""
    if not coppeliasim_available:
        pytest.skip("CoppeliaSim 未运行,跳过集成测试")
    srv = FactoryMCPServer()
    assert srv.ensure_connected(), "无法连接 CoppeliaSim"
    yield srv
    # 清理: 停仿真 + 断双连接
    if srv.is_running():
        srv.stop()
    if srv.ro_connector is not None:
        try:
            srv.ro_connector.client = None
        except Exception:
            pass
    srv.connector = None
```

- [ ] **Step 2: 验证 fixture 可被收集**

Run: `cd e:/Work/paper/cop-test && .venv/Scripts/python.exe -m pytest tests/ --collect-only -q`
Expected: 无语法错误;若 CoppeliaSim 未启动则后续集成测试会 skip(此处只验证能 collect)。

- [ ] **Step 3: 提交**

```bash
git add tests/conftest.py
git commit -m "test: add conftest with CoppeliaSim connection fixture"
```

---

## Task 2: ro_connector 基建 + ensure_ro_connected

**Files:**
- Modify: `src/factory_mcp_server.py` (FactoryMCPServer 类,约 [line 88-121](../../../src/factory_mcp_server.py#L88-L121) 附近)

**Interfaces:**
- Produces: `FactoryMCPServer.ro_connector` 属性、`FactoryMCPServer.ensure_ro_connected() -> bool`、`FactoryMCPServer.ro_sim` property;`ro_lock` 实例锁。
- Consumes: `CoppeliaConnector`(from factory_core)。

- [ ] **Step 1: 写失败测试**

```python
# tests/test_mcp_read_tools.py (本 task 只加这一项)
"""A 档读取工具集成测试。"""
import json
import pytest


@pytest.mark.usefixtures("mcp_server")
class TestRoConnector:
    def test_ro_connector_lazy_init(self, mcp_server):
        # ro_connector 初始为 None
        # (注意: 此测试需在 server 首次只读调用前跑,session scope 下顺序不保证,
        #  故只验证 ensure_ro_connected 后非 None)
        assert mcp_server.ensure_ro_connected() is True
        assert mcp_server.ro_connector is not None
        assert mcp_server.ro_connector.check_alive() is True

    def test_ro_sim_available(self, mcp_server):
        mcp_server.ensure_ro_connected()
        assert mcp_server.ro_sim is not None
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd e:/Work/paper/cop-test && .venv/Scripts/python.exe -m pytest tests/test_mcp_read_tools.py::TestRoConnector -v`
Expected: FAIL — `AttributeError: 'FactoryMCPServer' has no attribute 'ro_connector'`

- [ ] **Step 3: 实现 ro_connector + ensure_ro_connected**

在 `FactoryMCPServer.__init__` 末尾加属性,并在 `ensure_connected` 后新增方法。修改 `src/factory_mcp_server.py`:

在 `__init__` 内(`self._lock = threading.Lock()` 之后)加:
```python
        # 只读诊断专用连接 — 与主连接隔离,避免与主循环抢锁
        self.ro_connector: Optional[CoppeliaConnector] = None
        self.ro_lock = threading.Lock()
```

在 `ensure_connected` 方法后新增:
```python
    @property
    def ro_sim(self):
        """只读连接的 sim 句柄。"""
        return self.ro_connector.sim if self.ro_connector else None

    def ensure_ro_connected(self, host: str = "localhost",
                            port: int = 23000) -> bool:
        """惰性建立只读诊断连接 — Lazily establish the read-only connection.

        host/port 跟随主连接(若已连),否则用参数默认值。
        """
        # 主连接未连 → 拒绝(单一入口)
        if self.connector is None or not self.connector.check_alive():
            raise RuntimeError("未连接 CoppeliaSim, 先调用 connect")
        h = self.connector.host if self.connector else host
        p = self.connector.port if self.connector else port
        with self.ro_lock:
            if self.ro_connector is not None and self.ro_connector.check_alive():
                return True
            conn = CoppeliaConnector(host=h, port=p)
            if not conn.connect():
                self.last_error = f"无法建立只读连接 {h}:{p}"
                raise RuntimeError(self.last_error)
            self.ro_connector = conn
            return True
```

- [ ] **Step 4: 在 disconnect 里同时关闭 ro_connector**

修改 `disconnect` 工具(约 [line 500-513](../../../src/factory_mcp_server.py#L500-L513)):
```python
@mcp.tool()
def disconnect() -> str:
    """断开与 CoppeliaSim 的连接 (会先停止运行中的仿真)。"""
    if server.is_running():
        server.stop()
    server.connector = None
    server.controller = None
    server.sim_thread = None
    server.ro_connector = None  # 新增: 关闭只读连接
    return "已断开连接"
```

- [ ] **Step 5: 跑测试确认通过**

Run: `cd e:/Work/paper/cop-test && .venv/Scripts/python.exe -m pytest tests/test_mcp_read_tools.py::TestRoConnector -v`
Expected: PASS(2 个用例)。CoppeliaSim 未运行则 skip。

- [ ] **Step 6: 提交**

```bash
git add src/factory_mcp_server.py tests/test_mcp_read_tools.py
git commit -m "feat(mcp): add read-only second ZMQ connection (ro_connector)"
```

---

## Task 3: A 档 — get_object_pose

**Files:**
- Modify: `src/factory_mcp_server.py` (新增 `@mcp.tool()` 函数)
- Test: `tests/test_mcp_read_tools.py`

**Interfaces:**
- Consumes: `server.ensure_ro_connected()`, `server.ro_sim`, `sim.getObject`/`getObjectPose`/`getObjectPosition`/`getObjectOrientation`/`getObjectMatrix`。
- Produces: MCP 工具 `get_object_pose(alias_or_path, ref="world", components="pose")`。

- [ ] **Step 1: 写失败测试**

追加到 `tests/test_mcp_read_tools.py`:
```python
class TestGetObjectPose:
    def test_pose_default_components(self, mcp_server):
        # 先 build 一个已知对象;Mobile 分层下的 AGV 或 floor 应存在
        from factory_mcp_server import get_object_pose
        r = get_object_pose(alias_or_path="Floor")  # build_scene 创建的地板
        # 若场景未 build,返回失败串;若已 build,返回 JSON 含 pose
        if r.startswith("失败"):
            pytest.skip("场景未构建,跳过")
        d = json.loads(r)
        assert "alias" in d
        assert "pose" in d or "position" in d

    def test_unknown_alias_returns_failure_string(self, mcp_server):
        from factory_mcp_server import get_object_pose
        r = get_object_pose(alias_or_path="NoSuchObject_xyz")
        assert r.startswith("失败") or "找不到" in r
```

> 注: 测试依赖场景已 build。在 session fixture 里无法保证 build,故用 `pytest.skip` 容错。这是集成测试固有限制。

- [ ] **Step 2: 跑测试确认失败**

Run: `cd e:/Work/paper/cop-test && .venv/Scripts/python.exe -m pytest tests/test_mcp_read_tools.py::TestGetObjectPose -v`
Expected: FAIL — `ImportError: cannot import name 'get_object_pose'`

- [ ] **Step 3: 实现 get_object_pose**

在 `src/factory_mcp_server.py` 的工具区(在 `get_config` 工具后、`tail_log` 前插入)加:
```python
@mcp.tool()
def get_object_pose(alias_or_path: str, ref: str = "world",
                   components: str = "pose") -> str:
    """读取场景中物体的真实位姿 — Read an object's real pose from CoppeliaSim.

    这是 Q2 的核心工具:从 CoppeliaSim 物理引擎读回真实位姿,而非 Python
    端的逻辑坐标。支持读取单个物体。

    Args:
        alias_or_path: 物体别名或路径。别名本身不含 '/'(见 CLAUDE.md
                       「已知问题」第 1 条);若传入已含 '/' 前缀则原样用,
                       否则前加 '/'。
        ref:          参考系, "world"(默认) 或 "parent"。
        components:   返回分量: "pose"(x,y,z,qx,qy,qz,qw)/
                      "pos"(xyz)/"euler"(xyz+欧拉角)/"matrix"(4x4)。
    Returns:
        JSON 字符串 {alias, handle, ref, components, pose/...}, 或失败原因。
    """
    import json
    try:
        server.ensure_ro_connected()
    except Exception as e:
        return f"失败: {e}"
    sim = server.ro_sim
    # 路径归一化: 已含 '/' 前缀则原样用, 否则前加 '/'
    path = alias_or_path if alias_or_path.startswith("/") else "/" + alias_or_path
    try:
        h = sim.getObject(path)
    except Exception as e:
        return f"失败: 找不到对象 '{alias_or_path}': {e}"
    ref_handle = sim.handle_world if ref == "world" else sim.handle_parent
    try:
        if components == "pose":
            data = sim.getObjectPose(h, ref_handle)
            key = "pose"
        elif components == "pos":
            data = sim.getObjectPosition(h, ref_handle)
            key = "position"
        elif components == "euler":
            pos = sim.getObjectPosition(h, ref_handle)
            eul = sim.getObjectOrientation(h, ref_handle)
            data = {"position": pos, "euler": eul}
            key = "euler"
        elif components == "matrix":
            data = sim.getObjectMatrix(h, ref_handle)
            key = "matrix"
        else:
            return f"失败: 未知 components '{components}', 应为 pose/pos/euler/matrix"
        out = {"alias": alias_or_path, "handle": h, "ref": ref,
               "components": components, key: data}
        return json.dumps(out, ensure_ascii=False, indent=2)
    except Exception as e:
        server.last_error = f"get_object_pose 失败: {e}"
        return f"失败: {e}"
```

- [ ] **Step 4: 跑测试确认通过**

Run: `cd e:/Work/paper/cop-test && .venv/Scripts/python.exe -m pytest tests/test_mcp_read_tools.py::TestGetObjectPose -v`
Expected: PASS(场景未 build 则 skip)。

- [ ] **Step 5: 提交**

```bash
git add src/factory_mcp_server.py tests/test_mcp_read_tools.py
git commit -m "feat(mcp): add get_object_pose tool (Q2 core)"
```

---

## Task 4: A 档 — list_objects

**Files:**
- Modify: `src/factory_mcp_server.py`
- Test: `tests/test_mcp_read_tools.py`

**Interfaces:**
- Consumes: `sim.getObjects(i, type)`, `sim.getObjectAlias`, `sim.object_shape_type`/`object_dummy_type`/`object_joint_type`。
- Produces: MCP 工具 `list_objects(filter="*", object_type="all")`。

- [ ] **Step 1: 写失败测试**

追加到 `tests/test_mcp_read_tools.py`:
```python
class TestListObjects:
    def test_list_all(self, mcp_server):
        from factory_mcp_server import list_objects
        r = list_objects()
        if r.startswith("失败"):
            pytest.skip("未连接")
        d = json.loads(r)
        assert "objects" in d
        assert isinstance(d["objects"], list)
        assert d["count"] == len(d["objects"])

    def test_filter_prefix(self, mcp_server):
        from factory_mcp_server import list_objects
        r = list_objects(filter="Island_*")
        if r.startswith("失败"):
            pytest.skip("未连接")
        d = json.loads(r)
        # 若场景已 build, Island_ 前缀应有对象; 否则可能空
        for obj in d["objects"]:
            assert "Island_" in obj["alias"] or obj["alias"].startswith("_Island_")
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd e:/Work/paper/cop-test && .venv/Scripts/python.exe -m pytest tests/test_mcp_read_tools.py::TestListObjects -v`
Expected: FAIL — `ImportError: cannot import name 'list_objects'`

- [ ] **Step 3: 实现 list_objects**

在 `src/factory_mcp_server.py` 加(接在 `get_object_pose` 后):
```python
@mcp.tool()
def list_objects(filter: str = "*", object_type: str = "all") -> str:
    """列出场景中所有物体 — List scene objects by type and name filter.

    用于"不知道场景里有什么"的探索性诊断。

    Args:
        filter:       别名通配(filter="Island_*" 列工作岛前缀); "*" 列全部。
        object_type:  "all"|"shape"|"dummy"|"joint"|"..." 类型过滤。
    Returns:
        JSON {objects: [{alias, handle, type}], count}。
    """
    import fnmatch
    import json
    try:
        server.ensure_ro_connected()
    except Exception as e:
        return f"失败: {e}"
    sim = server.ro_sim
    type_map = {
        "all": None,
        "shape": sim.object_shape_type,
        "dummy": sim.object_dummy_type,
        "joint": sim.object_joint_type,
    }
    types_to_scan = [type_map[object_type]] if object_type in type_map \
        else [sim.object_shape_type, sim.object_dummy_type, sim.object_joint_type]
    if object_type == "all":
        types_to_scan = [sim.object_shape_type, sim.object_dummy_type,
                         sim.object_joint_type]
    results = []
    seen = set()
    for tval in types_to_scan:
        i = 0
        while i < 5000:
            try:
                h = sim.getObjects(i, tval)
            except Exception:
                break  # 越界即结束
            if h in seen:
                i += 1
                continue
            seen.add(h)
            try:
                alias = sim.getObjectAlias(h)
            except Exception:
                alias = ""
            # CoppeliaSim 别名可能含下划线前缀(如 _Island_A);统一匹配
            if filter == "*" or fnmatch.fnmatch(alias, filter) \
               or fnmatch.fnmatch(alias, "_" + filter):
                results.append({"alias": alias, "handle": h,
                                "type": object_type if object_type != "all"
                                else "mixed"})
            i += 1
    return json.dumps({"objects": results, "count": len(results)},
                      ensure_ascii=False, indent=2)
```

- [ ] **Step 4: 跑测试确认通过**

Run: `cd e:/Work/paper/cop-test && .venv/Scripts/python.exe -m pytest tests/test_mcp_read_tools.py::TestListObjects -v`
Expected: PASS。

- [ ] **Step 5: 提交**

```bash
git add src/factory_mcp_server.py tests/test_mcp_read_tools.py
git commit -m "feat(mcp): add list_objects tool"
```

---

## Task 5: A 档 — read_signal (Q1 正路)

**Files:**
- Modify: `src/factory_mcp_server.py`
- Test: `tests/test_mcp_read_tools.py`

**Interfaces:**
- Consumes: `sim.getStringSignal`, `sim.getFloatSignal`, `sim.getIntegerSignal`, `sim.getDoubleSignal`, `sim.setIntegerSignal`(测试用)。
- Produces: MCP 工具 `read_signal(name)`。

- [ ] **Step 1: 写失败测试**

追加到 `tests/test_mcp_read_tools.py`:
```python
class TestReadSignal:
    def test_read_string_signal(self, mcp_server):
        from factory_mcp_server import read_signal
        # 先写一个测试信号
        server = mcp_server
        server.ensure_ro_connected()
        sim = server.ro_sim
        sim.setIntegerSignal("mcp_test_sig", 123)
        r = read_signal(name="mcp_test_sig")
        d = json.loads(r)
        assert d["name"] == "mcp_test_sig"
        assert d["type"] == "integer"
        assert d["value"] == 123

    def test_read_missing_signal(self, mcp_server):
        from factory_mcp_server import read_signal
        r = read_signal(name="definitely_no_such_signal_xyz")
        d = json.loads(r)
        assert d["value"] is None
        assert d["type"] == "none"
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd e:/Work/paper/cop-test && .venv/Scripts/python.exe -m pytest tests/test_mcp_read_tools.py::TestReadSignal -v`
Expected: FAIL — `ImportError: cannot import name 'read_signal'`

- [ ] **Step 3: 实现 read_signal**

在 `src/factory_mcp_server.py` 加:
```python
@mcp.tool()
def read_signal(name: str) -> str:
    """读取 CoppeliaSim 信号 — Read a named signal (string/float/integer/double).

    这是 Q1(读取软件内命令行内容)的正路:Lua 脚本里用
    sim.setStringSignal("console_xxx", text) 暴露的控制台/状态文本,
    可通过本工具读回。自动探测信号类型。

    Args:
        name: 信号名。
    Returns:
        JSON {name, type, value}, 不存在则 type="none", value=null。
    """
    import json
    try:
        server.ensure_ro_connected()
    except Exception as e:
        return f"失败: {e}"
    sim = server.ro_sim
    try:
        # 按类型依次探测: string → integer → float → double
        v = sim.getStringSignal(name)
        if isinstance(v, str) and v:
            return json.dumps({"name": name, "type": "string", "value": v},
                              ensure_ascii=False)
        v = sim.getIntegerSignal(name)
        if v is not None:
            return json.dumps({"name": name, "type": "integer", "value": v},
                              ensure_ascii=False)
        v = sim.getFloatSignal(name)
        if v is not None:
            return json.dumps({"name": name, "type": "float", "value": v})
        v = sim.getDoubleSignal(name)
        if v is not None:
            return json.dumps({"name": name, "type": "double", "value": v})
        return json.dumps({"name": name, "type": "none", "value": None})
    except Exception as e:
        server.last_error = f"read_signal 失败: {e}"
        return f"失败: {e}"
```

> 注: CoppeliaSim 信号不存在时各 get 函数返回 `None` 而非抛异常,故用 `isinstance`/`is not None` 判定。

- [ ] **Step 4: 跑测试确认通过**

Run: `cd e:/Work/paper/cop-test && .venv/Scripts/python.exe -m pytest tests/test_mcp_read_tools.py::TestReadSignal -v`
Expected: PASS。

- [ ] **Step 5: 提交**

```bash
git add src/factory_mcp_server.py tests/test_mcp_read_tools.py
git commit -m "feat(mcp): add read_signal tool (Q1 via signal channel)"
```

---

## Task 6: A 档 — get_object_tree

**Files:**
- Modify: `src/factory_mcp_server.py`
- Test: `tests/test_mcp_read_tools.py`

**Interfaces:**
- Consumes: `sim.getObject` 取根句柄, `sim.getObjectsInTree(h, sim.handle_all, 0)`。
- Produces: MCP 工具 `get_object_tree(root_alias)`。

- [ ] **Step 1: 写失败测试**

追加到 `tests/test_mcp_read_tools.py`:
```python
class TestGetObjectTree:
    def test_tree_of_root(self, mcp_server):
        from factory_mcp_server import get_object_tree
        # 场景根级对象;若未 build 用 list_objects 找一个
        from factory_mcp_server import list_objects
        lo = list_objects(filter="*")
        d_lo = json.loads(lo)
        if not d_lo["objects"]:
            pytest.skip("场景为空")
        root_alias = d_lo["objects"][0]["alias"].lstrip("_")
        r = get_object_tree(root_alias=root_alias)
        d = json.loads(r)
        assert "root" in d
        assert "children" in d
        assert isinstance(d["children"], list)
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd e:/Work/paper/cop-test && .venv/Scripts/python.exe -m pytest tests/test_mcp_read_tools.py::TestGetObjectTree -v`
Expected: FAIL — `ImportError: cannot import name 'get_object_tree'`

- [ ] **Step 3: 实现 get_object_tree**

在 `src/factory_mcp_server.py` 加:
```python
@mcp.tool()
def get_object_tree(root_alias: str) -> str:
    """列出某对象的子树 — List an object's subtree (joints, shapes, children).

    用于排查机械臂关节、传送带组件等层级结构。模式参考 factory_robot.py
    / build_scene.py 中既有的 getObjectsInTree 用法。

    Args:
        root_alias: 根对象别名(不含 '/')。
    Returns:
        JSON {root, handle, children: [{alias, handle, type}], count}。
    """
    import json
    try:
        server.ensure_ro_connected()
    except Exception as e:
        return f"失败: {e}"
    sim = server.ro_sim
    path = root_alias if root_alias.startswith("/") else "/" + root_alias
    try:
        root_h = sim.getObject(path)
    except Exception as e:
        return f"失败: 找不到根对象 '{root_alias}': {e}"
    try:
        tree = sim.getObjectsInTree(root_h, sim.handle_all, 0)
        children = []
        for obj in tree:
            if obj == root_h:
                continue
            try:
                alias = sim.getObjectAlias(obj)
            except Exception:
                alias = ""
            children.append({"alias": alias, "handle": obj})
        return json.dumps({"root": root_alias, "handle": root_h,
                           "children": children,
                           "count": len(children)},
                          ensure_ascii=False, indent=2)
    except Exception as e:
        server.last_error = f"get_object_tree 失败: {e}"
        return f"失败: {e}"
```

- [ ] **Step 4: 跑测试确认通过**

Run: `cd e:/Work/paper/cop-test && .venv/Scripts/python.exe -m pytest tests/test_mcp_read_tools.py::TestGetObjectTree -v`
Expected: PASS。

- [ ] **Step 5: 提交**

```bash
git add src/factory_mcp_server.py tests/test_mcp_read_tools.py
git commit -m "feat(mcp): add get_object_tree tool"
```

---

## Task 7: B 档 — 状态码映射 + get_simulation_state

**Files:**
- Create: `tests/test_state_codes.py` (纯单测,无需 CoppeliaSim)
- Modify: `src/factory_mcp_server.py`

**Interfaces:**
- Produces: 模块级 `_STATE_CODE_NAMES: dict[int, str]` 映射;MCP 工具 `get_simulation_state()`。

- [ ] **Step 1: 写纯单测(失败)**

```python
# tests/test_state_codes.py
"""状态码映射纯单测 — 无需 CoppeliaSim。"""
from factory_mcp_server import _STATE_CODE_NAMES


def test_known_codes():
    assert _STATE_CODE_NAMES[0] == "stopped"
    # CoppeliaSim sim_simulation_* 常用码:
    # 0 stopped, 17/18/19 advancing (各模式), 8 paused/blocked, 16 paused
    # 具体值在实现时对齐 sim_simulation_* 常量
    assert 0 in _STATE_CODE_NAMES


def test_unknown_code_fallback():
    assert _STATE_CODE_NAMES.get(99999, "unknown") == "unknown"
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd e:/Work/paper/cop-test && .venv/Scripts/python.exe -m pytest tests/test_state_codes.py -v`
Expected: FAIL — `ImportError: cannot import name '_STATE_CODE_NAMES'`

- [ ] **Step 3: 实现 _STATE_CODE_NAMES + get_simulation_state**

在 `src/factory_mcp_server.py` 顶部工具区前(在 `server = FactoryMCPServer()` 后)加映射表。CoppeliaSim `sim_simulation_*` 状态码(4.10):
```python
# 仿真状态码 → 中文/英文释义 — simulation state code → readable name
# 来源: CoppeliaSim sim_simulation_* 常量 (4.10)
_STATE_CODE_NAMES = {
    0:  "stopped",            # sim_simulation_stopped
    8:  "advancing_running",  # sim_simulation_advancing_running
    16: "advancing_first",    # sim_simulation_advancing_firstafterstart
    17: "advancing_last",     # sim_simulation_advancing_lastbeforestop
    19: "paused",             # sim_simulation_paused (部分版本)
}
# 兜底: 未在表中的码返回 "unknown(<code>)"
```

> 实现注意: 实际码值需在 Task 实现 / 测试时用 `sim.getSimulationState()` 对照真实返回值校准;若与上表不符,以实测为准并更新表。这是计划中唯一允许"以实测校准"的点,因为不同 4.10.x 子版本码值可能微调。

加工具:
```python
@mcp.tool()
def get_simulation_state() -> str:
    """读取仿真状态码 — Lightweight sim state probe for freeze diagnosis.

    轻量探针: 只返回 getSimulationState() 码 + 释义 + sim_time。
    用于冻结排查时频繁探活(inspect_scene_state 的廉价子集)。

    Returns:
        JSON {state_code, state_name, sim_time}。
    """
    import json
    try:
        server.ensure_ro_connected()
    except Exception as e:
        return f"失败: {e}"
    sim = server.ro_sim
    try:
        code = sim.getSimulationState()
        sim_t = sim.getSimulationTime()
        name = _STATE_CODE_NAMES.get(code, f"unknown({code})")
        return json.dumps({"state_code": code, "state_name": name,
                           "sim_time": round(sim_t, 3)},
                          ensure_ascii=False)
    except Exception as e:
        server.last_error = f"get_simulation_state 失败: {e}"
        return f"失败: {e}"
```

- [ ] **Step 4: 跑测试确认通过**

Run: `cd e:/Work/paper/cop-test && .venv/Scripts/python.exe -m pytest tests/test_state_codes.py -v`
Expected: PASS。

- [ ] **Step 5: 提交**

```bash
git add src/factory_mcp_server.py tests/test_state_codes.py
git commit -m "feat(mcp): add get_simulation_state tool + state code map"
```

---

## Task 8: B 档 — inspect_scene_state

**Files:**
- Modify: `src/factory_mcp_server.py`
- Test: `tests/test_mcp_diagnose_tools.py` (新建)

**Interfaces:**
- Consumes: `sim.getSimulationState`, `getSimulationTime`, `getSimulationTimeStep`, `getObjects` + `getObjectPose`。
- Produces: MCP 工具 `inspect_scene_state(summary=True)`。

- [ ] **Step 1: 写失败测试**

```python
# tests/test_mcp_diagnose_tools.py
"""B 档诊断工具集成测试。"""
import json
import pytest


class TestInspectSceneState:
    def test_summary_mode(self, mcp_server):
        from factory_mcp_server import inspect_scene_state
        r = inspect_scene_state(summary=True)
        if r.startswith("失败"):
            pytest.skip("未连接")
        d = json.loads(r)
        assert "state_code" in d
        assert "sim_time" in d
        assert "object_count" in d
        assert "poses" not in d  # summary 模式不带全量位姿

    def test_full_mode(self, mcp_server):
        from factory_mcp_server import inspect_scene_state
        r = inspect_scene_state(summary=False)
        if r.startswith("失败"):
            pytest.skip("未连接")
        d = json.loads(r)
        assert "poses" in d
        assert isinstance(d["poses"], list)
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd e:/Work/paper/cop-test && .venv/Scripts/python.exe -m pytest tests/test_mcp_diagnose_tools.py::TestInspectSceneState -v`
Expected: FAIL — `ImportError: cannot import name 'inspect_scene_state'`

- [ ] **Step 3: 实现 inspect_scene_state**

在 `src/factory_mcp_server.py` 加:
```python
@mcp.tool()
def inspect_scene_state(summary: bool = True) -> str:
    """一次性场景快照 — One-shot snapshot of sim state + object poses.

    用于冻结排查:一次拿到状态码、sim_time、dt、物体真实位姿。
    summary=True 只给计数;summary=False 给全量位姿(可能很大)。

    Args:
        summary: True 只给计数; False 给全量物体位姿。
    Returns:
        JSON {state_code, state_name, sim_time, dt, object_count,
              poses?: [{alias, pose}]}。
    """
    import json
    try:
        server.ensure_ro_connected()
    except Exception as e:
        return f"失败: {e}"
    sim = server.ro_sim
    try:
        code = sim.getSimulationState()
        sim_t = sim.getSimulationTime()
        dt = sim.getSimulationTimeStep()
        # 枚举所有 shape+dummy
        objs = []
        for tval in (sim.object_shape_type, sim.object_dummy_type):
            i = 0
            while i < 5000:
                try:
                    h = sim.getObjects(i, tval)
                except Exception:
                    break
                objs.append(h)
                i += 1
        out = {
            "state_code": code,
            "state_name": _STATE_CODE_NAMES.get(code, f"unknown({code})"),
            "sim_time": round(sim_t, 3),
            "dt": round(dt, 4),
            "object_count": len(objs),
        }
        if not summary:
            poses = []
            for h in objs:
                try:
                    alias = sim.getObjectAlias(h)
                except Exception:
                    alias = ""
                try:
                    pose = sim.getObjectPose(h, sim.handle_world)
                except Exception:
                    pose = None
                poses.append({"alias": alias, "handle": h, "pose": pose})
            out["poses"] = poses
        return json.dumps(out, ensure_ascii=False, indent=2)
    except Exception as e:
        server.last_error = f"inspect_scene_state 失败: {e}"
        return f"失败: {e}"
```

- [ ] **Step 4: 跑测试确认通过**

Run: `cd e:/Work/paper/cop-test && .venv/Scripts/python.exe -m pytest tests/test_mcp_diagnose_tools.py::TestInspectSceneState -v`
Expected: PASS。

- [ ] **Step 5: 提交**

```bash
git add src/factory_mcp_server.py tests/test_mcp_diagnose_tools.py
git commit -m "feat(mcp): add inspect_scene_state tool"
```

---

## Task 9: B 档 — run_lua 护栏审计层(纯单测)

**Files:**
- Modify: `src/factory_mcp_server.py` (加审计函数,不含 MCP 工具)
- Test: `tests/test_mcp_diagnose_tools.py`

**Interfaces:**
- Produces: 模块级 `_audit_lua_code(code, confirm) -> tuple[bool, str]` + `_LUA_DANGEROUS_PATTERNS` 正则列表 + 常数 `_LUA_MAX_CHARS=8000`。这些是 run_lua 的护栏纯逻辑,先单测。

- [ ] **Step 1: 写失败测试(纯单测,无需 CoppeliaSim)**

追加到 `tests/test_mcp_diagnose_tools.py`:
```python
from factory_mcp_server import _audit_lua_code, _LUA_MAX_CHARS


class TestAuditLua:
    def test_safe_code_passes(self):
        ok, msg = _audit_lua_code('sim.setStringSignal("x", "hi")')
        assert ok is True

    def test_dangerous_needs_confirm(self):
        ok, msg = _audit_lua_code('os.execute("rm -rf /")')
        assert ok is False
        assert "confirm" in msg.lower()

    def test_dangerous_with_confirm_passes(self):
        ok, msg = _audit_lua_code('os.execute("rm -rf /")', confirm=True)
        assert ok is True

    def test_too_long_rejected(self):
        ok, msg = _audit_lua_code("x = " + "1" * (_LUA_MAX_CHARS + 1))
        assert ok is False
        assert "长度" in msg or "len" in msg.lower() or "8000" in msg

    def test_popen_caught(self):
        ok, msg = _audit_lua_code('local f = io.popen("ls")')
        assert ok is False
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd e:/Work/paper/cop-test && .venv/Scripts/python.exe -m pytest tests/test_mcp_diagnose_tools.py::TestAuditLua -v`
Expected: FAIL — `ImportError: cannot import name '_audit_lua_code'`

- [ ] **Step 3: 实现审计函数**

在 `src/factory_mcp_server.py` 加(在 `_STATE_CODE_NAMES` 后):
```python
import re

# run_lua 护栏 — 危险 Lua 调用正则 (软护栏, 靠 confirm 拦手滑)
_LUA_DANGEROUS_PATTERNS = [
    re.compile(r"\bos\.execute\b"),
    re.compile(r"\bio\.popen\b"),
    re.compile(r"\bos\.exit\b"),
    re.compile(r"\bsim\.removeObject\b"),
    re.compile(r"\bsim\.deleteModel\b"),
    re.compile(r"\bloadstring\b"),   # 沙箱已禁用, 命中即拒
]
_LUA_MAX_CHARS = 8000


def _audit_lua_code(code: str, confirm: bool = False) -> tuple[bool, str]:
    """软护栏审计 — audit user Lua before execution.

    Returns: (ok, message). ok=False 时 message 说明拒绝原因。
    """
    if len(code) > _LUA_MAX_CHARS:
        return False, f"代码过长({len(code)}>{_LUA_MAX_CHARS}), 拒绝执行"
    for pat in _LUA_DANGEROUS_PATTERNS:
        if pat.search(code):
            if not confirm:
                return False, (f"检测到危险调用 {pat.pattern}; "
                              "需显式 confirm=True 才放行")
    return True, "ok"
```

- [ ] **Step 4: 跑测试确认通过**

Run: `cd e:/Work/paper/cop-test && .venv/Scripts/python.exe -m pytest tests/test_mcp_diagnose_tools.py::TestAuditLua -v`
Expected: PASS。

- [ ] **Step 5: 提交**

```bash
git add src/factory_mcp_server.py tests/test_mcp_diagnose_tools.py
git commit -m "feat(mcp): add run_lua guardrail audit function (pure unit)"
```

---

## Task 10: B 档 — run_lua 执行机制(集成测试 + 工具)

**Files:**
- Modify: `src/factory_mcp_server.py`
- Test: `tests/test_mcp_diagnose_tools.py`

**Interfaces:**
- Consumes: `server.ensure_ro_connected`, `sim.createScript(sim.scripttype_customizationscript, wrapped, 0)`, `sim.getStringSignal("mcp_run_lua_result")`, `sim.removeObject`。
- Produces: MCP 工具 `run_lua(code, timeout=5.0, dry_run=False, confirm=False)` + `FactoryMCPServer._run_lua_lingering` 标志。

- [ ] **Step 1: 写失败集成测试**

追加到 `tests/test_mcp_diagnose_tools.py`:
```python
class TestRunLuaExecution:
    def test_simple_arithmetic(self, mcp_server):
        from factory_mcp_server import run_lua
        r = run_lua(code='sim.setStringSignal("mcp_run_lua_result", tostring(6*7))')
        d = json.loads(r)
        assert d["ok"] is True
        assert "42" in d["result"]
        assert d["lingering"] is False

    def test_capture_error(self, mcp_server):
        from factory_mcp_server import run_lua
        r = run_lua(code='error("intentional boom")')
        d = json.loads(r)
        assert d["ok"] is False
        assert "boom" in d["result"]

    def test_dangerous_blocked_without_confirm(self, mcp_server):
        from factory_mcp_server import run_lua
        r = run_lua(code='os.execute("echo hi")', confirm=False)
        d = json.loads(r)
        assert d["ok"] is False
        assert "confirm" in d["result"].lower()

    def test_dry_run_does_not_execute(self, mcp_server):
        from factory_mcp_server import run_lua
        r = run_lua(code='error("should not run")', dry_run=True)
        d = json.loads(r)
        assert d["ok"] is True
        assert "dry" in d["result"].lower() or "未执行" in d["result"]
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd e:/Work/paper/cop-test && .venv/Scripts/python.exe -m pytest tests/test_mcp_diagnose_tools.py::TestRunLuaExecution -v`
Expected: FAIL — `ImportError: cannot import name 'run_lua'`

- [ ] **Step 3: 实现 run_lua 工具**

机制(已实测): 用户代码嵌入定制脚本 `sysCall_init`,用 `pcall` 包裹,结果写 `mcp_run_lua_result` 信号,Python 回读。在 `src/factory_mcp_server.py` 加:
```python
# run_lua 信号通道
_RUN_LUA_SIGNAL = "mcp_run_lua_result"


def _build_run_lua_script(user_code: str) -> str:
    """把用户代码包成定制脚本源码 — wrap user code into a customization script.

    机制(实测): ZMQ sim 无 executeScriptCode; load/loadstring 在沙箱被禁。
    故把用户代码直接嵌入 sysCall_init, 用 pcall 包裹, 结果写信号回读。
    """
    return (
        "function sysCall_init()\n"
        "    local ok, err = pcall(function()\n"
        f"        {user_code}\n"
        "    end)\n"
        f"    local prev = sim.getStringSignal('{_RUN_LUA_SIGNAL}')\n"
        "    if not ok then\n"
        f"        sim.setStringSignal('{_RUN_LUA_SIGNAL}', "
        "'EXEC_ERROR:' .. tostring(err))\n"
        "    elseif not prev then\n"
        f"        sim.setStringSignal('{_RUN_LUA_SIGNAL}', 'NO_RETURN')\n"
        "    end\n"
        "end\n"
    )


@mcp.tool()
def run_lua(code: str, timeout: float = 5.0,
            dry_run: bool = False, confirm: bool = False) -> str:
    """远程执行 Lua 探针 — Execute arbitrary Lua (guarded).

    ⚠ 同步阻塞: 执行期间 ro_connector 被独占, 其他只读工具会排队等待。
    机制: 创建临时定制脚本, 用户代码嵌入 sysCall_init + pcall 包裹,
    结果经 mcp_run_lua_result 字符串信号回读 (ZMQ 无 executeScriptCode,
    且沙箱禁用 load/loadstring)。

    Args:
        code:     Lua 源码。结果请显式 sim.setStringSignal('mcp_run_lua_result', ...)。
        timeout:  秒, 超时则标记 lingering 并拒绝后续重叠调用 (无法安全 kill)。
        dry_run:  True 只审计不执行。
        confirm:  True 才放行含危险调用(os.execute/io.popen/...)的代码。
    Returns:
        JSON {ok, result, blocked_seconds, timeout, warning, lingering}。
    """
    import json
    import time
    # 护栏 ④: 审计
    ok, msg = _audit_lua_code(code, confirm=confirm)
    if not ok:
        return json.dumps({"ok": False, "result": msg,
                           "blocked_seconds": 0.0, "timeout": timeout,
                           "warning": None, "lingering": False},
                          ensure_ascii=False)
    if dry_run:
        return json.dumps({"ok": True,
                           "result": f"dry_run: 审计通过, 未执行 ({msg})",
                           "blocked_seconds": 0.0, "timeout": timeout,
                           "warning": None, "lingering": False},
                          ensure_ascii=False)
    # 检查 lingering (上一个 run_lua 未返回)
    if getattr(server, "_run_lua_lingering", False):
        return json.dumps({"ok": False,
                           "result": "上一个 run_lua 未返回(超时未 kill), "
                                     "请先 stop_simulation 重置",
                           "blocked_seconds": 0.0, "timeout": timeout,
                           "warning": "lingering", "lingering": True},
                          ensure_ascii=False)
    try:
        server.ensure_ro_connected()
    except Exception as e:
        return f"失败: {e}"
    sim = server.ro_sim
    # 先清信号
    try:
        sim.setStringSignal(_RUN_LUA_SIGNAL, "")
    except Exception:
        pass
    script_src = _build_run_lua_script(code)
    result_holder = {"ok": True, "result": "NO_RETURN", "warning": None,
                     "lingering": False}

    def _execute():
        try:
            h = sim.createScript(sim.scripttype_customizationscript,
                                script_src, 0)
            time.sleep(0.3)  # 让 sysCall_init 跑完
            try:
                res = sim.getStringSignal(_RUN_LUA_SIGNAL)
            except Exception:
                res = None
            if res:
                if res.startswith("EXEC_ERROR:"):
                    result_holder["ok"] = False
                result_holder["result"] = res
            try:
                sim.removeObject(h)
            except Exception:
                pass
        except Exception as e:
            result_holder["ok"] = False
            result_holder["result"] = f"EXEC_ERROR: {type(e).__name__}: {e}"

    t0 = time.time()
    th = threading.Thread(target=_execute, daemon=True,
                          name="mcp-run-lua")
    th.start()
    th.join(timeout=timeout)
    blocked = time.time() - t0
    if th.is_alive():
        # 超时, 线程仍在 → 标记 lingering, 拒绝后续重叠
        server._run_lua_lingering = True
        result_holder["ok"] = False
        result_holder["result"] = f"执行超时({timeout}s), 结果可能不完整"
        result_holder["warning"] = "timeout"
        result_holder["lingering"] = True
    return json.dumps({"ok": result_holder["ok"],
                       "result": result_holder["result"],
                       "blocked_seconds": round(blocked, 3),
                       "timeout": timeout,
                       "warning": result_holder["warning"],
                       "lingering": result_holder["lingering"]},
                      ensure_ascii=False)
```

> 注: lingering 标志需在 `stop_simulation`/`disconnect` 时重置为 False(见 Task 12)。

- [ ] **Step 4: 跑测试确认通过**

Run: `cd e:/Work/paper/cop-test && .venv/Scripts/python.exe -m pytest tests/test_mcp_diagnose_tools.py::TestRunLuaExecution -v`
Expected: PASS(4 个用例)。

- [ ] **Step 5: 提交**

```bash
git add src/factory_mcp_server.py tests/test_mcp_diagnose_tools.py
git commit -m "feat(mcp): add run_lua tool with verified createScript+signal mechanism"
```

---

## Task 11: C 档 — controller pause/resume 标志 + 主循环检测

**Files:**
- Modify: `src/factory_controller.py` (加 `_pause_requested` 标志 + 主循环检测点)
- Test: `tests/test_mcp_control_tools.py` (新建)

**Interfaces:**
- Produces: `FactoryController._pause_requested: bool` 属性;主循环 [line 297](../../../src/factory_controller.py#L297) 附近新增 pause 检测分支。
- Consumes: 现有主循环 `while self.sim_time < self.total_time` 结构。

- [ ] **Step 1: 写失败测试**

```python
# tests/test_mcp_control_tools.py
"""C 档运行时控制工具集成测试。"""
import json
import time as _t
import pytest


class TestPauseResumeFlag:
    def test_flag_default_false(self, mcp_server):
        # controller 未运行时无 flag, 故测 FactoryController 类属性约定
        from factory_controller import FactoryController
        # 检查类有 _pause_requested 约定 (实例化需 sim, 故只查 __init__ 签名)
        import inspect
        src = inspect.getsource(FactoryController.__init__)
        assert "_pause_requested" in src
```

> 注: pause/resume 的完整集成测试需 start_simulation 后才能验,放 Task 12 联调。

- [ ] **Step 2: 跑测试确认失败**

Run: `cd e:/Work/paper/cop-test && .venv/Scripts/python.exe -m pytest tests/test_mcp_control_tools.py::TestPauseResumeFlag -v`
Expected: FAIL — `_pause_requested` not in source。

- [ ] **Step 3: 实现 controller 标志 + 主循环检测**

修改 `src/factory_controller.py`:

在 `__init__` 内(`self.agv_handle: int = -1` 附近,约 [line 143](../../../src/factory_controller.py#L143))加:
```python
        # ── 用户主动暂停标志 — user-requested pause (区别于 zmq_monitor.comm_paused) ──
        self._pause_requested: bool = False
        self._pause_at_sim_time: float = 0.0
```

在主循环 [line 297](../../../src/factory_controller.py#L297) 的 `comm_paused` 分支后,新增 pause 分支:
```python
                # ── 2. 通信暂停检查 — Skip control if reconnecting ──
                if self.zmq_monitor.comm_paused:
                    sim.step()
                    self.sim_time = sim.getSimulationTime()
                    continue

                # ── 2b. 用户主动暂停 — user pause: 只推进物理, 跳过控制逻辑 ──
                if self._pause_requested:
                    sim.step()
                    self.sim_time = sim.getSimulationTime()
                    self.step_count += 1
                    continue
```

- [ ] **Step 4: 跑测试确认通过**

Run: `cd e:/Work/paper/cop-test && .venv/Scripts/python.exe -m pytest tests/test_mcp_control_tools.py::TestPauseResumeFlag -v`
Expected: PASS。

- [ ] **Step 5: 提交**

```bash
git add src/factory_controller.py tests/test_mcp_control_tools.py
git commit -m "feat(controller): add _pause_requested flag + main-loop pause branch"
```

---

## Task 12: C 档 — pause_simulation / resume_simulation 工具 + step_simulation 扩展

**Files:**
- Modify: `src/factory_mcp_server.py` (新增 pause/resume 工具, 改 step_simulation 签名)
- Test: `tests/test_mcp_control_tools.py`

**Interfaces:**
- Consumes: `server.controller._pause_requested`(来自 Task 11);`sim.step()`(扩 step)。
- Produces: MCP 工具 `pause_simulation()`, `resume_simulation()`, 扩展后的 `step_simulation(n=1)`。

- [ ] **Step 1: 写失败测试**

追加到 `tests/test_mcp_control_tools.py`:
```python
from factory_mcp_server import pause_simulation, resume_simulation, step_simulation


class TestPauseResumeStepN:
    def test_step_n_runs_multiple(self, mcp_server):
        # 需仿真已 start; 若未 start 则 step 返回提示
        # 先确保 stopped
        if mcp_server.is_running():
            mcp_server.stop()
        # 直接 step_n 未运行应提示
        r = step_simulation(n=3)
        assert "未运行" in r or "失败" in r

    def test_pause_resume_roundtrip(self, mcp_server):
        # 此测试依赖完整场景能跑(已知冻结问题);冻结时 start 后 status 会告警,
        # 测试探测到 sim_time 不推进则 skip 而非 fail。
        from factory_mcp_server import (start_simulation, stop_simulation,
                                        status)
        start_simulation(total_time=30.0)
        _t.sleep(2.0)  # 给启动探活窗口(5s)留时间, 至少看是否推进
        st = json.loads(status())
        if st.get("sim_time", 0) < 0.05:
            stop_simulation()
            pytest.skip("场景疑似冻结, 跳过 pause/resume 联调")
        pr = pause_simulation()
        assert "失败" not in pr
        _t.sleep(0.5)
        rr = resume_simulation()
        assert "失败" not in rr
        stop_simulation()
        # lingering 标志应被 stop 重置
        assert getattr(mcp_server, "_run_lua_lingering", False) is False
```

> 注: 此测试依赖完整场景能跑(已知冻结问题),可能 skip 或需简化场景。是集成测试固有限制。

- [ ] **Step 2: 跑测试确认失败**

Run: `cd e:/Work/paper/cop-test && .venv/Scripts/python.exe -m pytest tests/test_mcp_control_tools.py::TestPauseResumeStepN -v`
Expected: FAIL — `ImportError: cannot import name 'pause_simulation'`。

- [ ] **Step 3: 实现 pause/resume + 扩展 step**

在 `src/factory_mcp_server.py` 改现有 `step_simulation` 并加 pause/resume。

替换现有 `step_simulation`(约 [line 394-404](../../../src/factory_mcp_server.py#L394-L404)):
```python
@mcp.tool()
def step_simulation(n: int = 1) -> str:
    """单步或多步推进仿真 (stepping 模式)。

    在仿真已启动的前提下, 推进 n 个 SIM_STEP (每个默认 0.05s)。
    n 上限 1000 防失控。

    Args:
        n: 推进步数, 默认 1, 上限 1000。
    Returns:
        末步仿真时间 + 实际步数, 或失败原因。
    """
    if server.sim is None:
        return "未连接 CoppeliaSim, 先调用 connect"
    if not server.is_running():
        return "仿真未运行, 先调用 start_simulation"
    n = max(1, min(int(n), 1000))
    try:
        for _ in range(n):
            server.sim.step()
        t = server.sim.getSimulationTime()
        return f"已推进 {n} 步, sim_time={t:.3f}s"
    except Exception as e:
        server.last_error = f"step 异常: {e}"
        return f"step 失败: {e}"
```

新增 pause/resume 工具:
```python
@mcp.tool()
def pause_simulation() -> str:
    """暂停仿真控制逻辑 (stepping 模式下)。

    置 controller._pause_requested=True, 主循环只推进物理引擎、跳过控制逻辑
    (区别于 zmq_monitor.comm_paused 的通信故障降级)。resume 清标志恢复。

    Returns:
        操作结果 + 暂停时 sim_time。
    """
    if not server.is_running():
        return "仿真未运行, 先调用 start_simulation"
    ctrl = server.controller
    if ctrl is None:
        return "失败: controller 不存在"
    ctrl._pause_requested = True
    ctrl._pause_at_sim_time = server.sim.getSimulationTime()
    return (f"已请求暂停控制逻辑 @ sim_time="
            f"{ctrl._pause_at_sim_time:.3f}s; 物理 engine 仍推进, "
            "用 resume_simulation 恢复")


@mcp.tool()
def resume_simulation() -> str:
    """恢复仿真控制逻辑 (清除 pause 标志)。

    Returns:
        操作结果。
    """
    if not server.is_running():
        return "仿真未运行"
    ctrl = server.controller
    if ctrl is None:
        return "失败: controller 不存在"
    ctrl._pause_requested = False
    return "已恢复控制逻辑 (pause 标志已清除)"
```

- [ ] **Step 4: 在 stop 里重置 lingering 标志**

修改 `FactoryMCPServer.stop`(约 [line 230-250](../../../src/factory_mcp_server.py#L230-L250)),在 `with self._lock:` 块开头加:
```python
        # 重置 run_lua lingering 标志 (stop 后场景已重置)
        self._run_lua_lingering = False
```

- [ ] **Step 5: 跑测试确认通过**

Run: `cd e:/Work/paper/cop-test && .venv/Scripts/python.exe -m pytest tests/test_mcp_control_tools.py -v`
Expected: PASS(可能部分 skip 因冻结问题)。

- [ ] **Step 6: 提交**

```bash
git add src/factory_mcp_server.py tests/test_mcp_control_tools.py
git commit -m "feat(mcp): add pause/resume tools, extend step_simulation to n-steps"
```

---

## Task 13: --inspect 验证 + 文档更新

**Files:**
- Modify: `docs/MCP_USAGE.md`

**Interfaces:**
- 无新增接口;验证 20 个工具(原 10 + 新 10)。

- [ ] **Step 1: --inspect 打印工具清单**

Run: `cd e:/Work/paper/cop-test && .venv/Scripts/python.exe src/factory_mcp_server.py --inspect`
Expected: 打印 20 个工具(`connect, build_scene, disconnect, start_simulation, stop_simulation, step_simulation, status, get_config, set_config, tail_log` + 新 10 个 `get_object_pose, list_objects, read_signal, get_object_tree, get_simulation_state, inspect_scene_state, run_lua, pause_simulation, resume_simulation`)。

> 注: 原 step_simulation 改了签名但仍同名,工具总数应为 **19** 个(10 原 - 1 step_simulation 改签名 + 10 新 = 19)。以此数为准;若为 20 说明重复定义了 step_simulation,需检查。

- [ ] **Step 2: 更新 MCP_USAGE.md**

在 [docs/MCP_USAGE.md](../../MCP_USAGE.md) 的工具表格区,在「状态与配置」表后新增三档表(A/B/C),并更新顶部「10 个工具」为「19 个工具」。新增内容示例:

```markdown
## 19 个工具

(原表保留)

### A 档 — 读取 (Q1/Q2)

| 工具 | 说明 | 关键参数 |
|------|------|---------|
| `get_object_pose` | 读物体真实位姿 (pose/pos/euler/matrix) | `alias_or_path, ref=world, components=pose` |
| `list_objects` | 列场景物体 (按类型/别名过滤) | `filter=*, object_type=all` |
| `read_signal` | 读信号 (string/int/float/double 自动判类型) | `name` |
| `get_object_tree` | 列某对象子树 | `root_alias` |

### B 档 — 诊断

| 工具 | 说明 | 关键参数 |
|------|------|---------|
| `inspect_scene_state` | 一次性快照 (状态码+sim_time+物体位姿) | `summary=true` |
| `get_simulation_state` | 轻量状态探针 (冻结排查) | — |
| `run_lua` | 远程 Lua 探针 (带护栏: 审计/超时/lingering/confirm) | `code, timeout=5, dry_run, confirm` |

### C 档 — 运行时控制

| 工具 | 说明 | 关键参数 |
|------|------|---------|
| `pause_simulation` | 暂停控制逻辑 (物理仍推进) | — |
| `resume_simulation` | 恢复控制逻辑 | — |
| `step_simulation` | 单步/多步 (n 上限 1000) | `n=1` |
```

并在「运行模型」节加一段双连接说明:
```markdown
- **双连接**: 主连接 (写/控制类工具 + 主仿真循环) 与 ro_connector (只读诊断
  工具 + run_lua) 隔离, 避免运行中诊断工具与主循环互挤 (解决 CLAUDE.md
  「已知问题」第 4 条假断连)。
```

- [ ] **Step 3: 跑全量测试**

Run: `cd e:/Work/paper/cop-test && .venv/Scripts/python.exe -m pytest tests/ -v`
Expected: 纯单测(test_state_codes, test_audit)必过;集成测试视 CoppeliaSim 状态通过或 skip。

- [ ] **Step 4: 提交**

```bash
git add docs/MCP_USAGE.md
git commit -m "docs: update MCP_USAGE for 19 tools + dual-connection model"
```

---

## Self-Review checklist (执行前自查)

- [ ] 工具总数核验: 原 10 - step_simulation(改签名不新增) + 10 新 = **19**。Task 13 Step 1 验证。
- [ ] run_lua 机制已在 spec 实测: createScript(customizationscript) + 信号回读,load 禁用 → 代码嵌入。Task 10 落地。
- [ ] lingering 标志在 stop/disconnect 重置: Task 12 Step 4 + Task 2 Step 4(disconnect 置 ro_connector=None 时不影响 lingering, 但 stop 重置已覆盖)。
- [ ] 状态码映射允许实测校准: Task 7 注明。
- [ ] 集成测试 skip 路径: 无 CoppeliaSim 时 fixture skip,场景未 build 时各测 skip。
