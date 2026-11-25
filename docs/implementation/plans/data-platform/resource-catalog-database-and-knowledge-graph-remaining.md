---
title: 资源目录与知识图谱剩余工作 — 语义融合、发布与运维
type: plan
status: proposed
created: 2026-11-24
updated: 2026-11-25
scope: 数据平台 / 静态目录 / 知识图谱 / 未实施
related:
  - ../../reports/data-platform/v1-catalog-persistence.md
  - ../../reports/data-platform/v1-knowledge-graph-import.md
  - ../../reports/data-platform/v1-run-record-persistence.md
  - ../../archive-plans/data-platform/resource-catalog-database-and-knowledge-graph.md
---

# 资源目录与知识图谱剩余工作 — 语义融合、发布与运维

本方案只保留资源目录/知识图谱主题的**未实施**项。静态目录 SQLite 持久化、
运行记录 Q1–Q4、Neo4j dump 载入/导出和知识导入/发布已实施；已有报告见
[目录持久化](../../reports/data-platform/v1-catalog-persistence.md)、
[运行记录](../../reports/data-platform/v1-run-record-persistence.md)、
[知识图谱导入](../../reports/data-platform/v1-knowledge-graph-import.md)。
完整原始方案已归档到
[资源目录与知识图谱完整方案](../../archive-plans/data-platform/resource-catalog-database-and-knowledge-graph.md)。

## 1. 阶段 5.1：来源知识到可执行目录的语义融合

从已审核的 staging 记录开始，不重复 dump 识别/导入：

```text
已审核 staging
  → 人工语义映射（产品结构、工艺目标、操作、工步、参数、能力、设备/夹具）
  → 生成/更新静态 catalog staging release
  → ProcessCatalog.validate + 场景绑定/设备/资源/工步 allowlist 校验
  → 独立发布 catalog release
  → 应用启动选定 release，构建 RecipeSpec/OperationTemplate
```

第一批范围限定为：产品/部件语义、工艺目标、Operation 顺序、Action 到现有
work-step 的映射、Capability 到设备候选。参数驱动控制、动态夹具更换、在线
工艺推荐、自动生成可执行 Python、跨运行优化不纳入首批。

**工作步骤：**

1. 建立语义映射清单：源标签/谓词、目标对象、是否自动、人工责任人和缺失
   处理；保留原始 Neo4j ID/谓词/来源摘要，不把 `elementId` 当长期业务主键。
2. 定义产品结构与运行谱系边界：`include` 只有在确认表示设计组成/BOM 时
   映射为产品结构；运行时物料谱系只由 `MaterialRegistry` 的已验证提交
   产生，两者分表/分关系类型。
3. 生成可审查的 recipe 候选 JSON：按具体 product/Goal 查 Operation、
   `after` 顺序和 Action/Parameter；离线生成，不在仿真每步查询 Neo4j。
4. 执行可行性映射：每个 operation 明确 executor kind、capability、输入
   输出 ports、route/前置策略、resource policy、work-step ID 和 completion
   verifier；未知 action/parameter 进入 unresolved 报告并阻止发布。
5. 设备匹配与场景契约检查：capability/install 只生成候选；再用
   `ProcessCatalog.validate()`、`device_binding`、required aliases、布局/
   IK/碰撞和资源规则逐层筛选。
6. 双重发布：知识抽取发布与可执行目录发布是两个独立动作；任一步失败保留
   旧 current release，不自动切换。
7. 运行时钉住 recipe/catalog version、route、artifact ID 和 contract
   version；运行中目录不可变更。
8. 用 Q1–Q4 查询复核运行结果并反馈修订；不回写已发布运行事实。

**通过门槛：**映射清单可审计；catalog 候选通过结构、绑定和 allowlist
校验；未映射项阻止发布；旧 release 在失败时保持不变。

## 2. 阶段 6：运维与验证缺口

1. **备份/恢复演练：**按实际部署位置定义 SQLite/Neo4j 备份与恢复流程并
   演练；恢复后目录校验和来源摘要一致。
2. **新机器复现：**在没有 `ref/fmpmm.dump` 的环境使用已有导出 JSON 复现
   导入/发布；若需要真实 dump，重新执行官方 Neo4j 载入/导出，不能伪造。
3. **联合验收：**执行一次真实 `full_flow` 并检查运行记录（run、工序快照、
   UID 谱系、资源等待、证据）与目录版本一致；本轮未执行。
4. **保留策略：**当前运行记录全量保留、无删除命令；需定义保留期、归档和
   删除审批，不自动删除已发布运行事实。
5. **Q5/Q6 查询：**设备利用率/调度决策（Q5）和跨运行对比（Q6）未实现；
   `run_event` 中的 `SCHEDULE_*` 事件可作为后续扩展输入。
6. **自动化测试：**迁移、约束、往返、发布和边界检查按原方案 §8 范围；
   当前仍以手工验证为主，转为自动化时单独实施。

## 3. 不做项

- 不把 Neo4j 变成仿真运行时查询后端；SQLite 知识表仍是权威导入结果。
- 不把任意 Neo4j property 字典透传给 controller；参数必须有登记 schema。
- 不从图谱加载或调用任意代码；未知 action 只作为知识/待映射项。
- 不自动做模糊语义推断或自动发布；未确认的映射保持人工审核。
- 不修改或覆盖受保护的 `docs/scene_artifacts/v1/` 工件；目录版本变化需要
  新工件时走现有派生/验证流程。