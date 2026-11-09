---
title: v2 启动准备度与 v1 阶段盘点
type: assessment
created: 2026-11-09
updated: 2026-11-09
status: current
scope: flexible-factory v1 -> v2
---

# v2 启动准备度与 v1 阶段盘点

本文是启动 v2 规划时的当前状态总表。它汇总 v1 报告和验收证据，不替代各专题报告；具体测试结果、运行条件和原始证据以链接报告为准。当前盘点基于截至 2026-11-09 已归档资料，未在本次重新运行仿真或完整测试套件。

## 总体判断

v1 已形成可运行产品和订单工作台：受保护的 `L5_UFLOW` 工件支持 `full_flow`、静态工艺目录、工序 DAG、确定性能力筛选、订单 API/持久化、planner 草稿和前端；阶段 6 已在 CoppeliaSim 4.10 上完成限定矩阵的实机验收。可进入 v2 需求与基线阶段，但不应把 v1 的限定验收扩展解释为通用工厂调度或自动恢复能力。

业务阶段编号与订单 DAG 实施阶段编号是两套序列：业务阶段 0–4 已实现，业务阶段 5（调度优化与有限恢复）暂缓；订单 DAG 实施阶段 1–6 已完成各自限定交付和报告。两者不可用同一个“阶段 5/6”状态替代。

## 阶段盘点

| 工作域 | 状态 | 已交付 / 未完成 | 权威记录 |
|---|---|---|---|
| v1 产品基线与工件 | 已实现并验收 | `flexible-factory`、受保护 `L5_UFLOW` 工件、构建/加载/校验/运行入口；只覆盖登记场景和 `full_flow` 基线 | [产品实施报告](../reports/product/v1-product-implementation.md)、[场景工件说明](../../scene_artifacts/README.md) |
| 业务阶段 0–1：静态工艺目录 | 已实现 | 默认工艺目录及任务字段/状态投影接入 v1；不支持运行时用户自定义 recipe | [阶段 0–1 报告](../reports/production-flow/v1-stage-00-01-static-process-catalog.md) |
| 业务阶段 2：设备执行适配 | 已实现并 focused 验收 | 设备静态绑定、Arm/AGV executor 适配；不代表所有设备和运动故障都已验证 | [阶段 2 报告](../reports/production-flow/v1-stage-02-device-executor.md) |
| 业务阶段 3：工序 DAG | 已实现并 `full_flow` 验收 | 默认配方实例化、依赖/ready 集合、提交后解锁；尚无任意循环、返工或通用恢复 | [阶段 3 报告](../reports/production-flow/v1-stage-03-operation-dag.md) |
| 业务阶段 4：能力匹配调度 | 已实现并 `full_flow` 验收 | 确定性筛选、拒绝原因、设备选择和资源记录；默认 recipe 仍为固定路线/单设备候选 | [阶段 4 报告](../reports/production-flow/v1-stage-04-capability-scheduling.md) |
| 业务阶段 5：调度优化与有限恢复 | 暂缓，未实施 | 缺少业务批准的用例/比较基线；设备状态权威来源、尚未启动工序改派和现场恢复协议未具备 | [阶段 5 规划](../../wiki/06-工业案例/柔性车间/当前规划/06-23-混线生产阶段5调度优化与有限恢复.md) |
| 订单 DAG 阶段 1–3 | 已实现，逻辑/API/记录型执行验证 | 契约、校验与编译、失败隔离、版本化订单、worker/API 和 coordinator 执行 | [阶段 1–3 报告](../reports/order-orchestration/v1-order-dag-phase-01-03.md) |
| 订单 DAG 阶段 4：planner | 已实现，逻辑/API 验证 | 有界确定性 planner 生成并保存草稿；不自行启动仿真或选择未登记的工艺/设备 | [阶段 4 报告](../reports/order-orchestration/v1-order-dag-phase-04-auto-planner.md) |
| 订单 DAG 阶段 5：前端 | 已实现并集成验证 | 订单编辑、planner、运行查看；独立本机 API 消费端 | [阶段 5 报告](../reports/order-orchestration/v1-order-dag-phase-05-frontend.md) |
| 订单 DAG 阶段 6：仿真实机验收 | 已完成限定矩阵 | 手工/planner 订单、NODE 前置拒绝、GLOBAL 安全锁存通过；见报告列出的未执行项 | [阶段 6 报告](../reports/order-orchestration/v1-order-dag-phase-06-simulation-acceptance.md) |
| 目录/运行记录/知识图谱持久化 | 部分实现 | SQLite 目录、运行记录、Neo4j dump 导入链均有实现记录；真实 dump 的 Docker 端到端、备份恢复演练、记录保留策略及 Q5/Q6 查询仍有缺口 | [目录持久化报告](../reports/data-platform/v1-catalog-persistence.md)、[运行记录报告](../reports/data-platform/v1-run-record-persistence.md)、[知识图谱报告](../reports/data-platform/v1-knowledge-graph-import.md) |
| 本地数据库仪表盘前端优化 | 提案，未实施 | 现有 CLI/HTML 可查看数据；仪表盘改版方案与 UI 蓝图尚未形成实施报告 | [优化方案](../plans/data-platform/db-dashboard-frontend-optimization.md)、[UI 蓝图](../plans/data-platform/db-dashboard-ui-blueprint.md) |

## v2 前需管理的风险

| 风险 | 当前证据与边界 | v2 处理要求 |
|---|---|---|
| AGV 停靠与放置运动余量窄 | 阶段 2–4 报告记录停靠误差约 0.0195–0.0199 m，阈值 0.02 m；阶段 4 WP6 的 focused 复核未执行。运行时曾由既有 planner 选择水平绕行并完成，这不等同于稳定性证明 | 将物理基线复核设为涉及路线/布局/设备改派变更的前置门槛；不得靠放宽安全阈值消除失败 |
| 运动故障的局部隔离未获准入 | 阶段 6 只准入 `NODE_PRECHECK_REJECTED`；真实 Arm/AGV 运动类失败、真实 ZMQ 断连/step 故障仍按 GLOBAL 处理 | 保持失败关闭和人工现场确认；按单一故障类型、单一执行器做 focused 验收后再扩大分类 |
| 安全锁存后没有自动恢复 | 实机安全锁存验收进入 `ABORTED`，资源释放但锁存保留；`stop_confirmed=false` 需要操作员确认 | 先定义现场状态核验、物料/执行所有权及人工责任边界，再考虑有限恢复；不把 `mark_resumed()` 当通用复位 |
| 调度数据不足以支持优化/改派 | 当前默认 recipe 只有固定路线/单一候选；设备健康、工具载荷、姿态包络没有权威数据源，未登记替代设备不参与生产派工 | 先选单一业务目标和可重算指标，再补权威状态源及已验证候选；避免用静态能力声明代替物理可行性 |
| 验收与运行运维有范围缺口 | 阶段 6 使用受保护单场景；多订单并行、跨场景、MES/ERP、任意 DAG 和真实连接故障均未验收；Neo4j dump 真实 Docker 端到端及数据库备份/保留策略有缺口 | v2 需求中明确哪些是产品目标、哪些保持排除；只为选定交付补充相应运行和恢复演练 |

## 建议的 v2 进入顺序

1. 冻结 v2 产品目标和明确不做事项，选定一个用户工作流与业务指标；不要直接把业务阶段 5 的全部工作包视作已批准需求。
2. 选定固定工件、输入、seed、环境与对照指标，先复核 AGV 停靠/接触基线；基线不稳定时先处理稳定性，不混入调度策略收益。
3. 根据选定工作流补齐验收矩阵与未覆盖故障边界，再拆分可独立验证的实现包；对真实故障、改派和恢复逐项设准入条件。
4. v2 功能完成后更新本表、对应实施报告和证据索引；避免把规划状态、代码存在或 mock 测试写成实机验收结论。

## 文档归档处置

本轮保留阶段实施报告、阶段方案及业务规划：它们分别保存验收事实、实现约束和业务意图，内容虽有交叉引用但不能互相替代。统一阶段状态由本文和[项目文档登记表](project-document-register.md)维护；专题报告只保留本专题的实测、决策和未覆盖边界。旧阶段测试计数属于对应时间点的历史结果，不作为当前分支的最新测试结论。
