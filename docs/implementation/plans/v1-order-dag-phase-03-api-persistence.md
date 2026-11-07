---
title: 自建订单 DAG 阶段 3 — 订单 API 与版本化持久化
type: plan
status: implemented
created: 2026-11-06
updated: 2026-11-06
scope: v1 UFLOW / order service
related:
  - v1-self-built-order-dag-orchestration.md
  - v1-order-dag-phase-01-foundation.md
  - v1-order-dag-phase-02-failure-isolation.md
  - ../reports/v1-run-record-persistence.md
---

# 自建订单 DAG 阶段 3：订单 API 与版本化持久化

## 目标与边界

给订单领域模型提供可供前端和后端 planner 共用的服务接口、不可变版本存储和单一仿真运行入口。当前仓库没有 HTTP API 框架；实施前先确认部署形态与运行中的 CoppeliaSim 会话所有权，再选用最小兼容服务栈。本阶段不实现自动规划算法或前端。

## 运行所有权模型

采用单一仿真 worker 作为该 CoppeliaSim 实例的唯一所有者：worker 内创建并独占 ZMQ client、scene/runtime/coordinator，执行 `sim.step()` 并负责清理；HTTP handler 不持有 sim、scene、controller 或 handle，也不直接调用 CoppeliaSim API。API 通过命令队列提交 start/stop，通过只读状态快照和事件查询进度。若部署必须跨进程，队列可换成本机 IPC，订单/API 契约保持不变。

每个实例同一时刻只接受一个活动运行。worker 接受启动请求后创建 run 并返回 `run_id`；stop 请求进入队列，由 worker 在 stepping 边界执行受控停止。服务正常关闭时请求停止并等待 worker 清理；worker 崩溃或仿真连接丢失时不自动恢复，未完成 run 标记 `ABORTED` 并记录最后已知节点状态和断连原因。worker 重启后重新连接并校验场景，不从旧快照续跑。

## 具体步骤

1. 依据阶段 1 的 `OrderSpec` 与阶段 2 的状态契约定 API schema；字段校验和错误响应 code 复用领域 validator 诊断，不在 HTTP 层复制工艺规则。
2. 设计订单表/集合及版本存储：订单头保存当前版本指针；版本保存规范化 spec、digest、编排来源、创建时间、目录版本和场景 ID；运行关联保存 `order_id`、version、`run_id`、artifact ID、终态与报告引用。订单版本创建后不可原地覆盖。
3. 明确存储技术沿用项目 SQLAlchemy/Alembic 与 SQLite 的边界；增加迁移和 repository/service。订单 spec 与静态 `ProcessCatalog` release 分表/分责；不可通过 `catalog-source=db` 绕过其等价性校验。
4. 定义最小接口：读取可编排模板目录；创建订单；读取订单/版本；创建新版本；校验/预览 DAG；启动某个固定版本；读取运行状态、节点状态与事件；请求停止。每个运行请求必须绑定显式版本和已校验的 artifact。
5. 实现 application service orchestration：API 校验并保存不可变版本后，将含 order version/artifact ID 的启动命令投递给仿真 worker；worker 获取实例运行锁、加载/检查场景、构建 runtime/coordinator 并独占 stepping。禁止 HTTP handler 推进 sim 或直接操作 executor。
6. worker 接受启动并返回 run ID 后，API 请求快速响应；状态和事件查询从 worker 暴露的快照/事件通道读取。并发启动由实例锁原子拒绝；停止请求由 worker 在 stepping 边界处理。断连、worker 异常和服务关闭均按运行所有权模型收敛终态，不伪报成功。
7. 运行事件通过内存队列/已有报告边界汇总，再批量持久化；不在 `sim.step()` 中同步写 SQL。为节点/订单终态和局部失败传播保存足够字段重建执行过程。
8. 增加聚焦测试：repository 一组覆盖版本不可变与 digest；service/API 一组覆盖无效订单不启动、重复启动冲突和运行关联；将停止/异常终态纳入 service 生命周期用例，不为每个 endpoint 重复验证领域 validator。

## 主要落点

- 新增 `application/orders.py`（或等价应用服务）和独立订单 repository/models/migration。
- HTTP 框架放入单独服务入口/adapter，保持 `cli → application → domain` 依赖方向；domain 不依赖 HTTP 或 persistence。
- 复用 `persistence/runs.py` 的运行记录边界原则，但订单定义自身使用独立 schema。
- 新增 API contract、service、repository 与 runner-boundary 测试；扩展 `tests/test_v1_run_records.py` 验证 order linkage。

不单独增加订单字段逐项 round-trip、每个 endpoint 的同义校验或热路径 mock 矩阵；通过规范化 spec digest、代表性 service 流程和现有 stepping-boundary 测试验证契约。

## 验收与交付

- 手工订单和 planner 订单都能调用同一校验、预览、版本保存和运行服务。
- API 无法传入代码、场景句柄或未登记执行路径；运行启动绑定不可变订单版本和工件。
- 运行期间 step 热路径不写数据库；节点状态、故障范围、订单终态和报告可查询。
- 交付 API schema、错误码、数据库迁移、服务入口/启动配置、接口测试和运行生命周期说明。

## 进入下一阶段

接口契约和订单运行服务可在无前端条件下通过集成测试后，后端自主规划器开始直接输出阶段 1 的 `OrderSpec` 并调用同一 service。
