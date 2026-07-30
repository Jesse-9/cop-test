---
title: simROS2 接口 API 详解
type: reference
tags:
  - coppeliasim
  - 版本/v4.10
  - 类别/协同仿真
  - 难度/高级
created: 2026-07-24
updated: 2026-07-24
status: published
source:
  - simROS2.htm
  - ros2InterfaceApi.htm
  - ros2Interface.htm
  - index/simROS2.json
related:
  - "[[wiki/05-数字孪生与协同仿真/05-01-ROS2集成]]"
  - "[[wiki/05-数字孪生与协同仿真/05-04-ROS1集成]]"
  - "[[wiki/01-API参考/01-04-插件API总览]]"
  - "[[wiki/01-API参考/01-01-Regular-API-overview]]"
sim_version: "4.10"
---

# simROS2 接口 API 详解

> CoppeliaSim ROS2 Interface 插件（`libsimROS2.*`）暴露的 `simROS2.*` 命名空间 API 详解：话题发布/订阅、服务端/客户端、Action 状态机、TF、图像传输、参数服务器、时间与 QoS。

> 来源说明：`ros2InterfaceApi.htm` 仅是重定向到 `simROS2.htm` 的跳转页，实际 API 内容以 `simROS2.htm` 为准；函数清单与枚举/结构体以 `docs/raw/manual/index/simROS2.json` 为权威索引。

## 概述

ROS2 Interface 属于 CoppeliaSim API 框架，以较高保真度复刻 C++ ROS2 API。CoppeliaSim 可作为 ROS2 节点，通过 **话题（topic）**、**服务（service）**、**动作（action）** 与其他节点通信。插件以共享库提供，在脚本中 `require` 加载：

```python
simROS2 = require('simROS2')
```

```lua
simROS2 = require('simROS2')
```

加载前请 source ROS2 环境，并留意控制台/终端的插件加载日志。官方示例场景：

- `scenes/messaging/ros2InterfaceTopicPublisherAndSubscriber.ttt`
- `scenes/controlTypeExamples/controlledViaRos2.ttt`
- `models/tools/ros2Interface helper tool.ttm`（stepping 模式手动逐帧推进）

## 命名空间 API 总览

`simROS2` 命名空间在 `docs/raw/manual/index/simROS2.json` 中收录 **58 个函数**（与 `simROS2.htm` 字母序索引一致），另含 8 个枚举、2 个结构体、3 个回调签名，按用途分组如下。

### 话题（Topic）

| 函数 | 说明 |
|------|------|
| `createPublisher` | 创建话题发布者 |
| `createSubscription` | 创建话题订阅 |
| `publish` | 发布消息 |
| `shutdownPublisher` / `shutdownSubscription` | 关闭发布者/订阅 |
| `publisherTreatUInt8ArrayAsString` / `subscriptionTreatUInt8ArrayAsString` | 将 `uint8[]` 当字符串处理（Lua 更快） |

```python
string publisherHandle = simROS2.createPublisher(
    string topicName, string topicType,
    int unused=0, bool unused2=false, simros2_qos qos=nil)

string subscriptionHandle = simROS2.createSubscription(
    string topicName, string topicType, func topicCallback,
    int unused=0, simros2_qos qos=nil)

simROS2.publish(string publisherHandle, list message)
```

`topicType` 采用 ROS2 命名规范 `package/msg/MsgType`（双斜杠），如 `geometry_msgs/msg/Twist`、`sensor_msgs/msg/Image`。

### 服务（Service）

| 函数 | 说明 |
|------|------|
| `createService` | 创建服务端 |
| `createClient` | 创建服务客户端 |
| `call` | 调用服务 |
| `waitForService` | 阻塞等待服务可达 |
| `shutdownService` / `shutdownClient` | 关闭服务端/客户端 |
| `serviceTreatUInt8ArrayAsString` / `clientTreatUInt8ArrayAsString` | `uint8[]` 按字符串处理 |

```python
string serviceHandle = simROS2.createService(
    string serviceName, string serviceType, func serviceCallback)
string clientHandle = simROS2.createClient(
    string serviceName, string serviceType)
list result = simROS2.call(string clientHandle, list request)
bool result = simROS2.waitForService(string clientHandle, float timeout)
```

服务类型字符串为 `package/srv/SrvType`，如 `std_srvs/srv/Trigger`。

### 动作（Action）

ROS2 接口独有，ROS1 接口无此能力。完整覆盖 action server/client 与 goal 状态机：

| 函数 | 说明 |
|------|------|
| `createActionServer` | 创建 action 服务端 |
| `createActionClient` | 创建 action 客户端 |
| `sendGoal` | 客户端发送 goal |
| `cancelLastGoal` | 客户端取消最近 goal |
| `actionServerActionExecute` | 服务端：标记 goal 开始执行（仅 pending 时调用） |
| `actionServerActionSucceed` / `actionServerActionAbort` / `actionServerActionCanceled` | 服务端：标记 goal 成功/中止/取消（终态，调用后不得再操作该 goal） |
| `actionServerActionIsActive` / `actionServerActionIsExecuting` / `actionServerActionIsCanceling` | 服务端：查询 goal 是否 pending/executing/canceling |
| `actionServerPublishFeedback` | 服务端：发布 feedback |
| `shutdownActionServer` / `shutdownActionClient` | 关闭 |
| `actionServerTreatUInt8ArrayAsString` / `actionClientTreatUInt8ArrayAsString` | `uint8[]` 按字符串处理 |

```python
string actionServerHandle = simROS2.createActionServer(
    string actionName, string actionType,
    func handleGoalCallback, func handleCancelCallback, func handleAcceptedCallback)

string actionClientHandle = simROS2.createActionClient(
    string actionName, string actionType,
    func goalResponseCallback, func feedbackCallback, func resultCallback)

bool success = simROS2.sendGoal(string actionClientHandle, list goal)
bool success = simROS2.cancelLastGoal(string actionClientHandle)
simROS2.actionServerActionExecute(string actionServerHandle, string goalUUID)
simROS2.actionServerActionSucceed(string actionServerHandle, string goalUUID, list result)
simROS2.actionServerPublishFeedback(string actionServerHandle, string goalUUID, list feedback)
```

`actionName` 如 `/fibonacci`，`actionType` 为 `package/action/ActionType`。

### TF 变换

```python
simROS2.sendTransform(list transform)     # 单个 geometry_msgs/msg/TransformStamped
simROS2.sendTransforms(list transforms)   # 多个
```

### 图像传输（image_transport）

| 函数 | 说明 |
|------|------|
| `imageTransportCreatePublisher` / `imageTransportCreateSubscription` | 创建图像发布者/订阅 |
| `imageTransportPublish` | 发布图像（buffer + 宽高 + frame_id） |
| `imageTransportShutdownPublisher` / `imageTransportShutdownSubscription` | 关闭 |

```python
string pub = simROS2.imageTransportCreatePublisher(
    string topicName, int queueSize=1)
simROS2.imageTransportPublish(
    string publisherHandle, buffer data, int width, int height, string frame_id)
```

### 参数服务器

| 函数 | 说明 |
|------|------|
| `getParamBool` / `getParamInt` / `getParamDouble` / `getParamString` | 读取参数（返回 `exists, value`，支持 `defaultValue`） |
| `setParamBool` / `setParamInt` / `setParamDouble` / `setParamString` | 写入参数 |
| `hasParam` | 检查参数是否存在 |
| `deleteParam` | 删除参数 |

> 与 ROS1 接口相比，ROS2 接口**没有** `searchParam`。

### 时间

| 函数 | 说明 |
|------|------|
| `getTime` | 返回 `simros2_time` 结构体，可选 `clock_type`（默认 `ros`） |
| `getSimulationTime` | 仿真时间 |
| `getSystemTime` | 系统时间 |
| `timeToFloat` / `timeFromFloat` | 时间结构体与浮点秒互转 |

```python
simros2_time time = simROS2.getTime(int clock_type=simros2_clock_ros)
simROS2.getSimulationTime()
simROS2.getSystemTime()
simROS2.timeFromFloat(float t)
float t = simROS2.timeToFloat(list t)
```

### 消息接口工具（ROS2 独有）

| 函数 | 说明 |
|------|------|
| `createInterface` | 按类型构造默认值消息，如 `geometry_msgs/msg/Twist` |
| `getInterfaceConstants` | 取该接口定义的常量表 |
| `supportedInterfaces` | 列出已编译支持的所有接口类型名 |
| `importInterface` | 导入指定接口 |

```python
list msg = simROS2.createInterface('geometry_msgs/msg/Twist')
list consts = simROS2.getInterfaceConstants('geometry_msgs/msg/Twist')
list names = simROS2.supportedInterfaces()
simROS2.importInterface(string name)
```

### 执行循环

```python
simROS2.spinSome()   # 处理待派发的回调（无返回）
```

## 枚举与结构体

`simROS2.json` 定义了以下枚举/结构体（在脚本中通过同名表访问成员）：

| 名称 | 取值 |
|------|------|
| `clock_type` | `ros`、`system`、`steady` |
| `action_result_code` | `succeeded`、`aborted`、`canceled`、`unknown` |
| `goal_response` | `reject`、`accept_and_execute`、`accept_and_defer` |
| `cancel_response` | `reject`、`accept` |
| `qos_history_policy` | `system_default`、`keep_last`、`keep_all` |
| `qos_reliability_policy` | `system_default`、`reliable`、`best_effort` |
| `qos_durability_policy` | `system_default`、`transient_local`、`volatile` |
| `qos_liveliness_policy` | `system_default`、`automatic`、`manual_by_topic` |
| 结构体 `simros2_time` | ROS2 时间（秒 + 纳秒） |
| 结构体 `simros2_qos` | QoS 配置（history/reliability/durability/liveliness 等策略组合） |

回调函数签名（`scriptfun:` 前缀，由用户实现）：

```python
simROS2.subscriptionCallback(list message)          # 话题订阅回调
simROS2.imageTransportCallback(buffer data, int width, int height)  # 图像订阅回调
simROS2.actionGoalResponseCallback(string goalID, bool accepted)    # action goal 响应回调
```

## 消息类型支持

话题/服务/动作类型以 ROS2 命名规范字符串指定（双斜杠）：

- 话题：`package/msg/MsgType`，如 `geometry_msgs/msg/Twist`、`sensor_msgs/msg/Image`、`std_msgs/msg/Float32`
- 服务：`package/srv/SrvType`，如 `std_srvs/srv/Trigger`
- 动作：`package/action/ActionType`，如 `example_interfaces/action/Fibonacci`

消息以 dict/table 表示，键名与 ROS2 消息字段一致。已编译支持的类型可用 `supportedInterfaces()` 查询；需要扩展支持的消息/服务/动作，编辑插件 `meta/` 后重新编译（与 ROS1 插件同理）。

## 与 ROS2 标准 API 的对应

`simROS2` 是 C++ rclcpp / Python rclpy API 的脚本侧镜像，命名与语义对应明确：

| ROS2 概念 | C++ rclcpp | `simROS2` |
|-----------|-----------|-----------|
| 发布者 | `create_publisher<T>` | `createPublisher` |
| 订阅 | `create_subscription<T>` | `createSubscription` |
| 服务端 | `create_service<T>` | `createService` |
| 服务客户端 | `create_client<T>` | `createClient` |
| 调用服务 | `client->async_call` | `call`（同步） |
| 等待服务 | `wait_for_service` | `waitForService` |
| Action 服务端 | `rclcpp_action::create_server` | `createActionServer` + goal 状态机 |
| Action 客户端 | `rclcpp_action::create_client` | `createActionClient` + `sendGoal`/`cancelLastGoal` |
| spin | `executor.spin_some()` | `spinSome` |
| TF 广播 | `tf2_ros::TransformBroadcaster` | `sendTransform`/`sendTransforms` |
| QoS | `rclcpp::QoS` | `simros2_qos` 结构体 |
| 参数 | `node->declare_parameter` / `get_parameter` | `getParam*` / `setParam*` |

差异：`simROS2` 的 `call` 为同步调用（阻塞返回结果），而 rclcpp 原生为 `async_call` + future；脚本侧以同步更直观。句柄为字符串，生命周期由 `shutdown*` 显式管理。

## 典型用法：视觉传感器图像发布

以下脚本将视觉传感器图像发布到 `/image`（改编自官方 `ros2Interface.htm`），演示 `createPublisher` + `publisherTreatUInt8ArrayAsString` + `publish` + `getTime`：

```python
#python (simulation script, 适用 CoppeliaSim 4.10)
def sysCall_init():
    global sim, simROS2
    sim = require('sim')
    simROS2 = require('simROS2')
    self.visionSensor = sim.getObject('/Vision_sensor')
    self.pub = simROS2.createPublisher('/image', 'sensor_msgs/msg/Image')
    simROS2.publisherTreatUInt8ArrayAsString(self.pub)

def sysCall_sensing():
    img, resolution = sim.getVisionSensorImg(self.visionSensor)
    data = {
        'header':       {'stamp': simROS2.getTime(), 'frame_id': 'a'},
        'height':       resolution[1],
        'width':        resolution[0],
        'encoding':     'rgb8',
        'is_bigendian': 1,
        'step':         resolution[0] * 3,
        'data':         img,
    }
    simROS2.publish(self.pub, data)

def sysCall_cleanup():
    simROS2.shutdownPublisher(self.pub)
```

```lua
--lua (simulation script, 适用 CoppeliaSim 4.10)
function sysCall_init()
    sim = require('sim')
    simROS2 = require('simROS2')
    visionSensor = sim.getObject('/Vision_sensor')
    pub = simROS2.createPublisher('/image', 'sensor_msgs/msg/Image')
    simROS2.publisherTreatUInt8ArrayAsString(pub)
end

function sysCall_sensing()
    local img, resolution = sim.getVisionSensorImg(visionSensor)
    local data = {
        header       = {stamp = simROS2.getTime(), frame_id = 'a'},
        height       = resolution[2],
        width        = resolution[1],
        encoding     = 'rgb8',
        is_bigendian = 1,
        step         = resolution[1] * 3,
        data         = img,
    }
    simROS2.publish(pub, data)
end

function sysCall_cleanup()
    simROS2.shutdownPublisher(pub)
end
```

订阅端示例（命令话题驱动）：

```python
#python (simulation script, 适用 CoppeliaSim 4.10)
def cmd_callback(msg):
    # 收到 geometry_msgs/msg/Twist，写入驱动或日志
    sim.addLog(sim.verbosity_scriptinfos, f'cmd_vel: {msg["linear"]}')

def sysCall_init():
    global sim, simROS2
    sim = require('sim')
    simROS2 = require('simROS2')
    self.sub = simROS2.createSubscription(
        '/cmd_vel', 'geometry_msgs/msg/Twist', cmd_callback)

def sysCall_cleanup():
    simROS2.shutdownSubscription(self.sub)
```

## 参见

- [[wiki/05-数字孪生与协同仿真/05-01-ROS2集成]] — ROS2 集成教程与构建说明
- [[wiki/05-数字孪生与协同仿真/05-04-ROS1集成]] — simROS（ROS1）对照
- [[wiki/01-API参考/01-04-插件API总览]] — 插件 API 体系
- [[wiki/01-API参考/01-01-Regular-API-overview]] — Regular API 总览
- [[wiki/05-数字孪生与协同仿真/05-02-数字孪生概述]] — 数字孪生背景
