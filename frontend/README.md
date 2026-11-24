# 柔性工厂订单工作台 (frontend)

阶段 5 的独立前端 workspace。React + TypeScript + Vite + React Flow + Dagre，
只消费阶段 3/4 的本机订单 HTTP API，不直接访问 CoppeliaSim。

## 前置条件

- Node.js 20+（本仓库以 Node 24 验证）。
- 先启动订单 API：

```powershell
flexible-factory api serve --host 127.0.0.1 --port 8080
```

API 默认绑定 loopback，首期没有认证/TLS；不要把 API 或本工作台直接暴露到
共享网络。浏览器只编辑订单草稿和查看服务端快照，不管理执行器、设备句柄或
物料账本。

## 开发

```powershell
cd frontend
npm ci
npm run dev
```

Vite 开发服务器绑定 `127.0.0.1:5173`，将 `/api` 代理到
`http://127.0.0.1:8080`。可通过环境变量覆盖 API 目标：

```powershell
$env:VITE_DEV_API_TARGET = "http://127.0.0.1:8081"
npm run dev
```

如果前端与 API 不同源部署，使用反向代理保持同源 `/api`，或在服务端增加
显式 CORS allowlist；不要用通配来源替代部署配置。

## 构建与部署

```powershell
npm run build
npm run preview
```

`dist/` 是静态产物。部署时需把 `/api` 反向代理到订单 API，并为
`/orders/:id`、`/planner`、`/runs/:runId` 配置 SPA fallback 到
`index.html`。`VITE_API_BASE_URL` 可在构建时覆盖 API 基址（默认 `/api`）。

## 测试

```powershell
npm test          # Vitest: API client 错误映射、订单草稿模型、状态映射
npm run e2e       # Playwright: 手工订单、planner 草稿、订单方案目录、订单启动路径复用、运行失败视图
```

首次运行浏览器测试前执行：

```powershell
npm run e2e:install
```

E2E 使用 Playwright route mock 固定 API 响应，不需要 CoppeliaSim 或真实
订单服务。真实 API/planner 的集成验证另行用本机 API 执行；真实仿真订单与
故障准入由阶段 6/7/9 验收，结果见
[阶段 6 实施报告](../docs/implementation/reports/order-orchestration/v1-order-dag-phase-06-simulation-acceptance.md)、
[阶段 7 实施报告](../docs/implementation/reports/order-orchestration/v1-order-dag-phase-07-dual-fleet.md)
与[阶段 9 实施报告](../docs/implementation/reports/order-orchestration/v1-order-dag-phase-09-scene-profile-order-catalog.md)。

阶段 6/7/9 的真实 UI 验收脚本（需要 API、Vite、CoppeliaSim）：

```powershell
$env:ACCEPT_ARTIFACT_ID = "v1/L5_UFLOW/v005_20261117_110924"
node e2e/real_acceptance.mjs manual   # 通过真实 UI 创建 full_flow 订单并启动
$env:ACCEPT_FLEET = "dual"            # 在启动对话框选择双车并断言运行页
node e2e/real_acceptance.mjs manual
node e2e/real_acceptance.mjs planner  # 通过 planner UI 保存草稿并启动

# assembly_mvp 工件：API/Vite 都要指向实验工件根
#   flexible-factory api serve --artifact-root docs/scene_artifacts/experiments/assembly-mvp
$env:ACCEPT_ARTIFACT_ID = "v1/L5_UFLOW/v001_20261114_120734"
node e2e/real_acceptance.mjs mvp-manual   # 手工创建压缩机订单并启动
node e2e/real_acceptance.mjs mvp-planner  # planner 生成压缩机订单并启动
$env:ACCEPT_PRODUCT = "FLANGE_ASSY"       # 切换到法兰产品
node e2e/real_acceptance.mjs mvp-manual
node e2e/real_acceptance.mjs mvp-planner

node e2e/real_acceptance.mjs start    # 重新启动 ORDER_ID 指定的订单
RUN_ID=<run_id> node e2e/real_acceptance.mjs verify  # 刷新前后状态恢复核验
```

`EXPECT_NODES=7` 可在刷新核验时等待节点表加载完成；MVP 订单设为 `1`。
脚本不使用 route mock；`NO_START=1` 可只创建/保存订单而不启动仿真。

## 页面

| 路由 | 页面 |
|---|---|
| `/orders` | 订单列表、订单方案目录（机械臂/运输/差异 chips 与一键创建）、搜索/筛选、最近运行 |
| `/orders/new` | 新建空白订单、模板 palette、DAG 画布、检查器、预览/保存 |
| `/orders/:orderId` | 订单版本、digest、planner 元数据、预览、启动（车队/路径复用）、运行历史 |
| `/orders/:orderId/edit` | 从当前版本创建新版本 |
| `/planner` | 目标/投料/约束表单、候选与 explain、保存草稿 |
| `/runs/:runId` | 只读运行快照、节点表、失败详情、事件时间线、受控停止请求 |

## 边界

- 浏览器只做即时编辑提示；服务端 `POST /api/orders/preview` 是校验权威。
- 订单方案目录来自 `GET /api/order-variants`，创建只保存普通订单版本，
  不自动启动；前缀/运输变体标 compiler-only/transit，不冒充生产实机能力。
- 订单启动对话框默认 `CACHE_THEN_PLAN`；路径复用只覆盖五臂 `PLACING`，
  v005 已有记录，v002/MVP 没有记录时会回退在线规划。`CACHE_ONLY` 缺记录
  会使运行失败，适合固定动作回归而不适合日常启动。
- 物料/模板/区域下拉来自 `/api/templates` 的 `scene_profile` 能力；页面不再
  假定固定物料集合。切换“场景工件”后，跨 profile 草稿会显示诊断并阻止保存，
  可手动清理不兼容节点/投料。`profile_source=unbound` 只表示 API 未绑定工件。
- MVP 直接来料逻辑节点没有“自动按区域/类型”入口：新增投料表单和每笔投料行
  都提供显式 `node_id`/`port_id` 选择；未绑定时 preview 会返回
  `ORDER_INPUT_NO_PORT`，不会静默猜测。
- 画布位置不属于 `OrderSpec`；自动布局只改视图，不改变节点/边语义。
- 运行页只显示服务端返回的字段；缺失字段显示“未记录”，不推断成功/安全。
- 订单版本不可变；编辑器保存总是创建新版本。
- `planner` 只生成静态可行草稿；设备健康、节拍、实时资源、AGV 排队、几何
  可达性、碰撞和运动规划可行性不在前端或 planner 模型内。
- 窄屏以列表/分区视图为主；画布始终提供节点表、连线表和键盘连接表单。
