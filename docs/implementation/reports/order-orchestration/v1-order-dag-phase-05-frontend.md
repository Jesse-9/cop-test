---
title: 自建订单 DAG 阶段 5 实施报告 — 前端订单与流程编排
type: report
status: review
created: 2026-11-07
updated: 2026-11-07
scope: v1 UFLOW / order UI
related:
  - ../../plans/order-orchestration/v1-order-dag-phase-05-frontend.md
  - ../../plans/order-orchestration/v1-order-dag-phase-05-ui-design-spec.md
  - ../../plans/order-orchestration/v1-order-dag-phase-03-api-persistence.md
  - ../../plans/order-orchestration/v1-order-dag-phase-04-auto-planner.md
  - v1-order-dag-phase-01-03.md
  - v1-order-dag-phase-04-auto-planner.md
---

# 自建订单 DAG 阶段 5 实施报告

本报告记录
[阶段 5 前端方案](../../plans/order-orchestration/v1-order-dag-phase-05-frontend.md) 与
[UI 设计规格](../../plans/order-orchestration/v1-order-dag-phase-05-ui-design-spec.md) 的实施结果。
阶段 5 新增独立 `frontend/` workspace，只消费阶段 3/4 的本机订单 HTTP API，
不访问 CoppeliaSim、executor、设备句柄或物料账本。

## 1. 交付概览

| 交付 | 内容 |
|---|---|
| 工程骨架 | React 19 + TypeScript strict + Vite 7 + React Flow 12 + Dagre；独立 `frontend/` workspace |
| API client | 类型化 DTO、超时/网络/非 JSON/4xx 错误映射、模板/订单/版本/预览/启动/运行/planner 接口 |
| 订单列表 | 搜索结果与筛选、版本/来源/最近运行、目标摘要、空态/离线/错误态 |
| 订单编辑器 | 模板 palette、React Flow DAG、检查器、订单目标/投料、键盘连接表单、撤销/重做、自动布局、服务端 preview、保存新版本 |
| Planner 工作台 | `PlannerInput` 表单、目标/投料/允许范围/路线偏好/搜索上限、候选/拒绝原因/join 绑定/未建模维度、保存草稿并编辑 |
| 运行详情 | 只读节点状态图/节点表/事件时间线、失败范围与阻断来源、受控 stop 请求、URL 恢复 |
| 质量 | 三栏桌面布局、窄屏分区、语义化表单/状态文字、键盘等价列表、`prefers-reduced-motion` |
| 测试 | Vitest 12 passed；Playwright 3 journeys passed；`npm run build` 通过 |
| 文档 | `frontend/README.md`、本报告、使用指南和工程参考更新 |

## 2. 目录与职责

```text
frontend/
  src/
    api/          client.ts, types.ts（唯一 API DTO/错误映射）
    domain/       orderDraft.ts（草稿/序列化/本地检查）, layout.ts, status.ts
    components/   AppHeader, TemplatePalette, OrderCanvas, Inspector,
                  StartRunDialog, ui
    pages/        OrdersPage, OrderDetailPage, OrderEditorPage,
                  PlannerPage, RunPage
    hooks/        useHealth
    test/         vitest setup
  e2e/            journeys.spec.ts, mock.ts
  README.md       启动/构建/部署/测试说明
```

浏览器只维护 `DraftState` 和视图状态：

- `DraftState` 序列化为阶段 1 `OrderSpec` 后调用服务端；
- `node_id` 由前端生成稳定、唯一 ID，不生成运行时 UID/设备 handle；
- 画布位置、选中态、撤销栈不进入 `OrderSpec`；
- 保存总是创建新的不可变订单版本，不覆盖旧版本。

## 3. 页面与交互

### 3.1 订单列表和详情

- 列表从 `GET /api/orders` 读取，再按当前版本读取目标摘要；筛选只作用于
  已取回数据。
- 详情显示当前/选定版本、digest、目录版本、来源、目标、投料、节点/连线数、
  planner 元数据和 planner explain（若存在）。
- 详情自动调用版本 preview；只有 preview 通过时才显示可启动的启动面板。
- 启动面板要求场景工件引用；启动后导航到 `/runs/{run_id}`。

### 3.2 订单编辑器

- 左侧模板 palette 按执行器/kind 分组、可搜索；显示语义、端口、能力、设备。
- 中间 React Flow 画布使用自定义工序节点和端口 handle；支持拖线、节点/边
  选择、删除、复制、缩放、适配视图、自动布局。
- 右侧检查器：
  - 未选择时编辑订单目标/投料；
  - 选择节点时显示模板约束、前驱/后继、删除/复制；
  - 选择边时显示端口映射和删除；
  - 始终提供“键盘连接”表单（起点/终点/端口）。
- 底部提供节点列表、连线列表和本地/服务端诊断；诊断可定位到节点/边。
- 服务端 `POST /api/orders/preview` 是权威校验；保存按钮只在当前草稿的
  preview 通过且 preview 后未修改时启用。
- 撤销/重做只覆盖编辑命令；自动布局只改视图位置。

### 3.3 Planner 工作台

- 表单与阶段 4 `PlannerInput` 对齐：目标物料/数量、投料、场景、允许模板/
  设备、路线偏好、搜索上限、`allow_high_risk`（首期仅展示保留字段）。
- 调用 `POST /api/planner/plan` 展示：可行/无解、最先阻断类别、选中分支、
  digest、投料消耗、join 绑定、候选与拒绝原因、搜索统计、未建模维度。
- 可行结果可调用 `POST /api/planner/drafts` 保存 planner 草稿，再进入编辑器
  修改；无解保留输入并显示后端诊断。

### 3.4 运行详情

- 通过 `GET /api/runs/{run_id}`、nodes、events 加载，活动运行按有界轮询
  刷新；停止只发送服务端 stop 请求，不由浏览器关闭仿真。
- 顶部固定运行 ID、订单版本/digest、工件、订单终态、失败范围、开始/结束；
  `PARTIAL_FAILED` 和 `ABORTED` 有独立页面提示。
- 画布为只读节点状态图；节点状态映射 `PENDING/WAITING/RUNNING/BLOCKED/
  DONE/FAILED/REJECTED/CANCELLED`，并显示 failure scope/code、阻断来源、
  输入/输出 UID 和证据。
- 节点表与画布选择联动；事件时间线显示服务端事件字段；刷新/复制 URL 后按
  run ID 恢复服务端状态。
- 停止按钮显示“请求中/已提交”；最终状态由轮询状态决定，不自行推断。

## 4. API 契约

前端只使用阶段 3/4 已存在的接口；唯一服务端增量是为 UI 路线偏好提供
`GET /api/templates` 的只读 `routes` 摘要（route_id/group/semantics/
operation_ids/entry_zone/exit_zone）。没有新增执行/设备/仿真控制接口。

错误映射：`NETWORK`/`TIMEOUT`/`INVALID_RESPONSE`/`HTTP_*`；服务端
`error.code`、`message`、`path`、`diagnostics` 原样进入界面，不在前端
重新解释工艺规则。HTTP 422/409 显示为页面内错误，不用短暂 toast 承载。

## 5. 测试与验证

### 5.1 单元/组件逻辑

```text
npm test
Test Files  3 passed
Tests       12 passed
```

- `src/api/client.test.ts`：成功响应、422 diagnostic、网络失败、非 JSON；
- `src/domain/orderDraft.test.ts`：重复模板节点 ID、合法/自环/重复/物料
  不连续边、删除节点级联、OrderSpec 往返、本地检查；
- `src/domain/status.test.ts`：终态/活动态、状态 tone、失败范围、digest。

### 5.2 Playwright journeys

```text
npm run e2e
3 passed
```

三条旅程均使用 route mock，不需要 CoppeliaSim/真实订单服务：

1. 手工订单：添加两个节点、键盘连接、添加投料、服务端 preview、保存；
2. planner：生成候选、保存草稿、进入编辑器；
3. 运行视图：`PARTIAL_FAILED` 显示失败/阻断分支，`ABORTED` 显示全局中止。

首次运行前需 `npm run e2e:install`。

### 5.3 构建与真实 API 集成

```text
npm run typecheck   # 通过
npm run build       # 通过（React/Flow vendor chunk 拆分）
```

另外以本机真实订单 API 验证了 `/api/health`、`/api/templates` 和
`/api/planner/plan`：同一目标/投料返回 `housing_direct → bracket_route`，
UI 能显示 7 道工序、digest、投料消耗和 `kit_join` 绑定。该验证不启动
CoppeliaSim，不代表真实订单运行通过。

Python 全量回归仍为 **598 passed / 3 skipped**（阶段 5 只增加
`/api/templates` 的只读 `routes` 摘要和对应测试）。

## 6. 限制

- 首期 API 无认证/TLS；前端按本机工程工具部署。跨主机/共享部署必须先设计
  访问控制，并配置同源反向代理或显式 CORS allowlist。
- 运行页采用有界轮询；阶段 3 没有事件游标/SSE/WebSocket 契约，前端不虚构
  实时推送。
- 前端本地检查只改善编辑体验；不得用浏览器规则替代服务端 validator/
  compiler，也不从缺失字段推断安全或成功。
- React Flow/Dagre 布局是视图辅助；布局不改变订单语义，静态图可行不代表
  物理运动可行。
- planner 页面只展示阶段 4 的静态候选；设备健康、节拍、实时资源、AGV
  排队、几何可达性、碰撞和运动规划可行性不在模型内。
- 阶段 6 真实 CoppeliaSim 订单/故障验收已完成，见
  [阶段 6 实施报告](v1-order-dag-phase-06-simulation-acceptance.md)；本报告
  本身仍不构成物理准入证据，真实 Arm/AGV 运动故障和连接断开仍未准入。

## 7. 版本与配置

- frontend package 版本 `0.1.0`；Node 24 / Vite 7 / React 19 验证。
- 开发 API 目标：`VITE_DEV_API_TARGET`（默认 `http://127.0.0.1:8080`）。
- 生产 API 基址：`VITE_API_BASE_URL`（默认 `/api`）。
- 订单契约仍为 `v1_order_20261106`；planner 策略为
  `v1_planner_20261107`；静态目录版本 `v1_process_20261106a`。
- 没有修改场景工件、仿真配置或 `config_digest`。
