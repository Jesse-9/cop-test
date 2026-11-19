# Neo4j 导出数据（可读格式）

- 源导出 JSON：`docs\implementation\reference\data-platform\neo4j-graph-export\fmpmm_graph.json`
- 原始 dump：`ref\fmpmm.dump`
- dump SHA-256：`9413d53dd5b6206e5f6082bf134757a3aa8f28e367f2fb95bea094cfdc75e344`
- 导出时间：`2026-11-13T16:19:58+00:00`
- 抽取器：`flexible_factory.neo4j_export` / `1`
- 节点：**222**；关系：**384**

## 文件

| 文件 | 用途 |
|---|---|
| `nodes.csv` | 节点表，含标签、名称和全部属性；UTF-8 BOM，可直接用 Excel 打开 |
| `relationships.csv` | 关系表，含关系类型和两端节点的标签/名称 |
| `graph.graphml` | 标准图格式，可导入 Gephi / yEd / Cytoscape |
| `graph.cypher` | 可读的 Cypher `CREATE` 语句，可在空 Neo4j 库中重放 |

## 标签分布

| 标签 | 数量 |
|---|---|
| `Instance` | 155 |
| `Abstraction` | 18 |
| `Goal` | 17 |
| `Operation` | 12 |
| `Parameter` | 9 |
| `Action` | 7 |
| `Capability` | 4 |

## 关系类型分布

| 关系类型 | 数量 |
|---|---|
| `include` | 131 |
| `has_instance` | 68 |
| `achieve_goal` | 48 |
| `has_capability` | 42 |
| `operation` | 24 |
| `has_action` | 23 |
| `has_parameter` | 22 |
| `after` | 21 |
| `installed_on` | 5 |

## 生成命令

```powershell
uv run python tools/neo4j_export_formats.py `
  --export docs\implementation\reference\data-platform\neo4j-graph-export\fmpmm_graph.json `
  --out-dir docs\implementation\reference\data-platform\neo4j-graph-export
```
