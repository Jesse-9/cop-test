---
title: 自建订单 DAG 阶段 5 — 前端订单与流程编排
type: plan
status: implemented
created: 2026-11-06
updated: 2026-11-07
scope: v1 UFLOW / order UI
related:
  - v1-self-built-order-dag-orchestration.md
  - v1-order-dag-phase-03-api-persistence.md
  - v1-order-dag-phase-04-auto-planner.md
  - v1-order-dag-phase-06-simulation-acceptance.md
  - v1-order-dag-phase-05-ui-design-spec.md
---

# 自建订单 DAG 阶段 5：前端订单与流程编排

## 目标与产品边界

建立给工艺/仿真工程人员使用的订单工作台：从已发布模板目录手工选取工序并连接成 DAG，或提交目标让后端 planner 生成候选图；预览服务端诊断、保存不可变订单版本、启动指定版本，并在运行期间查看节点、物料、设备和故障状态。手工图与 planner 图进入同一 `OrderSpec`、validator/compiler、保存和运行服务。

仓库当前没有前端工程。建议新增独立 `frontend/` workspace，使用 React、TypeScript、Vite、React Flow；流程布局先用 Dagre 处理简单有向无环图。如需要分层图的交叉减少和 compound layout，再评估 ELK，首版不同时引入两套布局引擎。组件库不预先锁定；实施前确认许可证、键盘行为与视觉定制能力后再选轻量方案。HTTP API 仍是阶段 3 的本机订单服务，前端部署必须明确同源静态托管或开发代理/CORS，不将 CoppeliaSim 接口暴露给浏览器。

界面采用工程工作台而非营销式首页：中性浅色工作区、稳定的状态颜色、紧凑数据字体和清楚的节点/边选中态；用画布、左侧可搜索模板目录和右侧检查器形成固定工作结构。订单定义与运行快照分离；进入运行视图后显示版本和 digest，草稿变更不得改变已启动运行。

## 参考实现与适用边界

- [React Flow](https://reactflow.dev/) 及其 drag-and-drop、validation、save/restore 示例：参考自定义工序节点、端口交互、边校验和图状态序列化。这里只复用编辑器交互，不把 React Flow 节点模型当作订单领域模型。
- [Node-RED Editor Guide](https://nodered.org/docs/user-guide/editor/)：参考节点 palette、workspace、wire、选择节点后编辑属性的工作流组织。其运行语义和扩展体系不直接移植。
- [ELK.js](https://eclipse.dev/elk/reference/algorithms/org-eclipse-elk-layered.html)：仅作为 DAG 自动布局备选；先测当前订单图规模和可读性，再决定是否引入。
- [WAI-ARIA Authoring Practices](https://www.w3.org/WAI/ARIA/apg/)：参考键盘焦点、按钮/对话框等控件模式；画布另提供等价的节点列表和边列表，避免只靠拖拽或颜色表达信息。

这些参考用于验证交互模式，不要求复制其视觉设计或扩大 v1 功能范围。

## 交互信息架构

| 页面/区域 | 主要内容 | 主要操作 |
|---|---|---|
| 订单列表 | 名称、来源、当前版本、目标、最近运行和状态 | 新建、打开、复制为新草稿 |
| 订单编辑器 | 可搜索模板 palette、DAG 画布、选择器/检查器、校验摘要 | 添加/删除节点、连接端口、编辑订单入口/目标、预览、保存 |
| 自动规划 | 目标物料/数量、可用投料、场景、允许范围和路线偏好 | 生成候选、查看解释、载入草稿、另存版本 |
| 运行详情 | 固定订单版本/digest、节点状态图、事件时间线、物料流/谱系、失败面板 | 刷新、请求停止、切换节点详情 |

桌面采用 palette / canvas / inspector 三栏；窄屏不压缩三栏，而切成模板、画布、详情三个视图，并保留当前选择和未保存状态。节点列表与运行表格视图始终可达，支持搜索和键盘选中。

## 具体实施阶段

### 5.1 工程骨架和 API 契约

1. 建立独立 frontend workspace、TypeScript strict、Vite、格式/lint 命令和环境配置；Python runtime 不依赖 Node 构建产物。
2. 从 `src/flexible_factory/api/server.py` 对齐现有 API：`GET /api/health`、`GET /api/templates`、`GET/POST /api/orders`、版本读取/创建、`POST /api/orders/preview`、planner plan/drafts、`POST .../start`、`GET /api/runs/{id}`、nodes/events 和 stop。不要规划不存在的接口。
3. 建唯一 API client 与前端 DTO；明确 4xx 领域诊断、409 版本/运行冲突、服务不可用和超时的映射。服务端仍是校验及运行状态权威来源。
4. 确定 API base URL、开发代理及本机访问边界；部署若不同源，明确服务端 CORS allowlist，不能用通配来源替代部署配置。

验收：健康状态、模板目录、订单列表能加载；模拟服务离线、空列表、422 和 409 能得到明确状态视图。以一组 API client/service 测试覆盖正常响应和错误映射，不为每个路由复制同义测试。

### 5.2 订单列表、草稿和版本

1. 实现订单列表、订单详情/版本切换及“新建空白订单”；列表仅依赖服务端数据，排序/筛选先在已取回数据内完成。
2. 编辑状态使用前端 view model，保存时显式序列化为 `OrderSpec`；节点稳定身份使用订单 spec 的 `node_id`，不在浏览器生成运行时 UID、设备 handle 或执行器状态。
3. 加入未保存标记、离开页面提示、保存中/保存成功/冲突状态。版本不可变；编辑已保存版本必须创建新版本，不做覆盖式 autosave。
4. 空订单、无版本、读取失败和只读运行版本均有明确空态/禁用动作。

验收：可创建并重开一份订单草稿；连续保存创建新版本，旧版本内容不变。通过一个代表性 service 流程测试保存与重载，不额外为每个字段编写 UI round-trip 测试。

### 5.3 DAG 编辑器

1. 从模板目录构造 palette 分组和搜索索引。卡片/详情展示工序语义、输入输出端口、executor/kind、候选设备、资源、场景绑定、join 能力及模板 ID；数据只读来自服务端目录。
2. 采用 React Flow 自定义工序节点和端口 handle。添加节点时仅选模板并赋予订单内稳定 `node_id`；边保存 source/target node ID、source/target port ID。删除节点时同步移除关联边并提示影响。
3. 实现端口拖线、边选中/删除、节点复制、缩放/适配视图、撤销/重做和自动布局。首版历史仅针对 spec 的节点/边/入口/目标编辑做有限命令栈，不保存服务端运行数据。
4. 添加即时前端交互约束：端口方向、已知端口、禁止自环、重复边和明显非法目标；这些只改善编辑体验，不宣称取代后端 validation。
5. 用服务端 preview 输出作为权威检查结果。每条 diagnostic 通过 `path`、code 和 node/edge/port 标识定位；错误列表可跳转并选中相关元素。未能关联到画布对象的诊断仍显示完整 code/message。
6. 同步提供键盘可操作的节点列表、边列表、属性表单和“连接到”选择器，覆盖无法使用精确拖拽的用户；可视状态不只依赖颜色。

验收：通过代表性图验证加点、端口连线、删除、自动布局、preview 诊断定位及从服务端重载。DAG/端口/环/物料规则负例由阶段 1 API/领域测试负责，不在浏览器复制完整负例矩阵。

### 5.4 Planner 生成与人工修订

1. 以表单提交目标物料/数量、投料、scene ID、模板/设备范围和路线偏好，字段与阶段 4 `PlannerInput` 对齐。
2. 提供“生成候选”预览和进度/失败提示；展示候选节点/边、目标物料路径、投料消耗、join 绑定、选择理由、未选候选排除原因和未建模限制。
3. 无解时展示后端 diagnostics，不把无解伪装成空画布。成功结果可载入当前草稿并人工修改；planner 保存的版本标明 `source=planner`，修改后另存新版本。
4. Planner 页面不包含本地路径搜索、工序优先级规则或另一套 compiler。

验收：一个 planner golden case 可载入、编辑、另存；一个参数化无解/输入错误 case 显示诊断。稳定选择规则继续由阶段 4 测试负责。

### 5.5 编译预览、保存与启动

1. 把预览/保存/启动做成顺序明确的操作：本地草稿 → 服务端 preview → 保存新版本 → 选择版本/artifact → 启动确认。
2. 启动确认摘要包含订单名、版本、digest、场景工件和目标；启动请求显式引用不可变版本与 artifact，不直接启动未保存的客户端图。
3. 处理中锁定重复提交；409 运行冲突、422 预览错误、artifact 不匹配和 worker 未就绪分别呈现恢复操作。
4. 启动成功后导航至该 `run_id`，运行视图只展示服务端快照。

验收：无效图不能启动；预览 digest 与保存/启动版本一致；重复启动冲突可恢复。由一个 API 集成旅程验证，不做每个对话框的重复 E2E。

### 5.6 运行观察、局部失败和全局停止

1. 运行视图通过状态接口首载并按 events 的游标/现有契约增量刷新；若阶段 3 没提供游标，则先按限量轮询状态与事件，不在前端假设 WebSocket/SSE。
2. 将事件折叠到节点状态：`PENDING/READY/RUNNING/DONE/FAILED/BLOCKED/CANCELLED`；颜色配合文字/图标/形状，节点详情列出等待原因、选中设备、输入/输出物料 UID、时间和 failure code。
3. NODE 故障突出失败节点和原因链，显示 BLOCKED 后继及仍可执行分支；GLOBAL/ABORTED 显示全局中止原因和其他活动节点终态。界面不自行推断故障范围或订单终态。
4. 事件时间线、节点表和画布选择联动；提供物料谱系/UID 关联视图。长时间线分页或限制渲染数量，完整记录由服务端返回。
5. 停止按钮只发送服务端 stop 请求，显示“请求中/已停止/无法确认”状态；页面关闭、网络断开或轮询失败不隐式发送停止。
6. 刷新或复制运行 URL 后按 run ID 恢复服务端状态，不依赖仅存在内存的 React state。

验收：一个局部失败运行样例证明 blocked 与独立分支并存；一个全局故障样例证明中止状态醒目且未完成节点未显示 DONE。无需真实仿真来验证 UI；真实物理准入由阶段 2 和阶段 6 验收负责。

### 5.7 质量与交付

1. 完成键盘焦点、表单标签、语义化错误、dialog 返回焦点、图表文字替代、200% 缩放和 prefers-reduced-motion。
2. 覆盖桌面宽屏与窄屏；窄屏以分区/抽屉或单列视图呈现，不让 palette/inspector 挤压画布。
3. 增加前端启动/构建说明、API 地址配置、订单工作台截图或简短操作说明。
4. 控制测试为三条 Playwright 旅程：手工订单预览并保存；planner 结果载入并另存；运行中局部故障/全局中止状态。组件级测试只覆盖关键诊断映射和状态展示；接口领域负例复用后端测试证据。

## 主要代码落点

- `frontend/`：Vite app、API client、订单编辑状态、编辑器、planner 表单、运行观察页。
- `src/flexible_factory/api/server.py` 及应用服务：只在部署/contract 缺口已证实时补静态托管或 CORS；前端工作不改仿真 worker 所有权。
- API DTO 以既有 Python schema 为准；可用 JSON Schema/codegen 生成类型时再引入工具，不维护两份手工领域模型。
- 不实现浏览器侧执行器、设备句柄、物料账本、工艺 validator 或 DAG scheduler。

## 风险与回退

- React Flow 社区/许可证/自定义端口和键盘行为实施前需确认；若不满足，可保留 API/DTO/service 层，替换画布 adapter，不重写订单域模型。
- DAG 布局先采用最少依赖；只有真实样例在自动布局后仍不可读时才增加 ELK。
- 本机 API 当前默认 loopback 且不含认证；UI 作为本机工程工具部署。跨主机或共享部署不属于本阶段，必须先单独设计访问控制。
- API 若缺失运行事件分页/游标，前端使用有界轮询；不得因此把数据库写入或仿真控制塞入浏览器。

## 验收与阶段出口

- 用户可手工创建合法 DAG、查看服务端 compile preview、保存不可变版本并启动指定版本。
- 阶段 4 planner 结果可解释、载入、人工修改并另存；两种入口共享 API 和领域校验。
- 运行视图能恢复、显示局部/全局失败语义并发出受控停止请求；浏览器不直接管理 CoppeliaSim。
- 精简测试为 API client 错误映射、关键编辑器行为和三条 E2E 旅程；领域规则不重复测试。
- 通过后进入阶段 6，按完整仿真订单链路和故障准入计划做真实 CoppeliaSim 验收。
