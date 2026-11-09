# 本地 Neo4j 工具

本目录只用于把已确认来源的 `fmpmm.dump` 通过官方 `neo4j-admin` 载入本地
Neo4j，并只读导出为知识导入 JSON。它不解析二进制 dump，也不把 Neo4j 作为
`flexible_factory` 的权威存储。

参考实现位于用户提供的 `ref/scripts/`：

- `import-neo4j-dump.ps1`：`neo4j-admin database load` → `database migrate`；
- `verify_neo4j.sh`：验证 `Instance`/`Goal`、`achieve_goal`、
  `has_capability`、`installed_on` 等图结构。

本目录的脚本去掉了硬编码密码，并使用项目命名卷
`cop-test-neo4j-data` / 容器 `cop-test-neo4j`。

## 前置条件

- Docker Desktop 已启动（`docker version` 能同时返回客户端和服务端）；
- 仓库根目录执行；
- 已执行 `uv sync --group dev`（包含 `neo4j` Python 驱动）。

## 1. 设置密码（当前 shell）

```powershell
$env:NEO4J_AUTH = 'neo4j/ReplaceWithYourPassword'
$env:NEO4J_PASSWORD = 'ReplaceWithYourPassword'
```

不要把密码写入 `docker-compose.yml`、脚本、`.env` 或 Git。

## 2. 载入 dump

`load-dump.ps1` 会停止 compose 服务、覆盖命名卷中的 `neo4j` 数据库、
迁移到 Neo4j 5 格式并重启服务。该操作会覆盖本地已有图谱数据。

```powershell
.\tools\neo4j\load-dump.ps1 -DumpPath .\ref\fmpmm.dump
```

脚本要求交互确认；自动化环境可显式传 `-Force`。首次执行会创建
`cop-test-neo4j-data` 卷。

## 3. 只读导出为 JSON

```powershell
uv run python .\tools\neo4j_export.py `
  --source-dump .\ref\fmpmm.dump `
  --out .\docs\implementation\runs\fmpmm_graph.json
```

导出器只执行 `MATCH ... RETURN`，不写 Neo4j。JSON 包含：

- `source.sha256`：原始 dump 摘要；
- `nodes`：`id`、`labels`、`properties`；
- `relationships`：`id`、`type`、`start`、`end`、`properties`。

## 4. 导入 SQLite 并审核发布

```powershell
flexible-factory knowledge import `
  --export .\docs\implementation\runs\fmpmm_graph.json `
  --source-dump .\ref\fmpmm.dump

flexible-factory knowledge status
flexible-factory knowledge verify --extraction-id <extraction_id>
flexible-factory knowledge publish --extraction-id <extraction_id>
```

`knowledge import` 只创建 staging extraction。未映射的标签/谓词、端点缺失
或 canonical ID 冲突会写入报告并阻止发布；需先扩展
`src/flexible_factory/persistence/knowledge.py` 中的
`ENTITY_TYPE_MAP` / `PREDICATE_MAP` 并审核数据。

## 5. 手工验收查询（来自 ref/scripts/verify_neo4j.sh）

```powershell
docker exec cop-test-neo4j cypher-shell -u neo4j -p $env:NEO4J_PASSWORD `
  "MATCH (i:Instance) WHERE i.pro_code IN ['TESTBOX','TESTCOVER','AUR0001','AUR0002','GDH0001','GDH0002'] RETURN i.pro_code, i.name ORDER BY i.pro_code;"
```

```powershell
docker exec cop-test-neo4j cypher-shell -u neo4j -p $env:NEO4J_PASSWORD `
  "MATCH (i:Instance)-[:achieve_goal]->(g:Goal) WHERE id(g) = 90 RETURN i.pro_code, i.name ORDER BY i.pro_code;"
```

这些查询验证了来源图的基本结构，不代表 SQLite 知识表已经发布；发布状态以
`flexible-factory knowledge status` 为准。

## 6. 停止与删除

```powershell
docker compose -f .\tools\neo4j\docker-compose.yml stop
docker compose -f .\tools\neo4j\docker-compose.yml down
```

`down` 保留命名卷；只有确认不再需要本地图谱时才执行
`docker volume rm cop-test-neo4j-data`（破坏性操作）。

## 验收记录

本地 dump 的载入、导出和知识导入验收结果由
[知识图谱实施报告](../../docs/implementation/reports/data-platform/v1-knowledge-graph-import.md)
记录。换用 dump 或修改映射后，应按本指南重新执行并更新报告；本文件只维护操作步骤。

## 限制

- 本目录不提供 Neo4j 生产部署、集群、插件或公网暴露。
- Neo4j 载入和导出需要本机 Docker；如果 Docker 不可用，只能使用已导出的
  JSON 进行 SQLite staging 和测试，不能声称已完成真实 dump 导入。
- 二进制 dump 的格式、版本和官方恢复方式由 `neo4j-admin` 校验；
  本项目不实现自己的 dump 解析器。
