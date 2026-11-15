#!/usr/bin/env python
"""把 Neo4j 导出 JSON 转成便于人工阅读的格式.

输入是 ``tools/neo4j_export.py`` 生成的
``cop-test-neo4j-export/1`` JSON; 本脚本不读取二进制 dump, 只消费已导出的
节点/关系数据, 因此 dump 的 SHA-256 会原样记录在输出摘要中.

输出 (默认 ``var/neo4j_export/``):

* ``nodes.csv`` / ``relationships.csv``: UTF-8 BOM, 可直接用 Excel 打开;
* ``graph.graphml``: 可导入 Gephi / yEd / Cytoscape;
* ``graph.cypher``: 可读的 CREATE 语句, 可在空 Neo4j 库中重放;
* ``README.md``: 来源、摘要、标签/关系类型统计.

Example (PowerShell):

    uv run python tools/neo4j_export_formats.py `
      --export docs/implementation/runs/fmpmm_graph.json `
      --out-dir var/neo4j_export
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
import xml.etree.ElementTree as ET
from collections import Counter
from pathlib import Path
from typing import Any

from flexible_factory.persistence.knowledge import EXPORT_FORMAT

NODE_FIXED_COLUMNS = ("id", "labels")
REL_FIXED_COLUMNS = (
    "id", "type",
    "start_id", "start_labels", "start_name",
    "end_id", "end_labels", "end_name",
)
PREFERRED_PROPERTY_COLUMNS = ("name", "pro_code", "pro_工艺编号")


def _cell(value: Any) -> str:
    """把属性值转成适合 CSV/GraphML 的字符串."""
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (dict, list, tuple)):
        return json.dumps(value, ensure_ascii=False, sort_keys=True)
    return str(value)


def _columns(rows: list[dict[str, Any]], preferred: tuple[str, ...] = ()) -> list[str]:
    keys = {key for row in rows for key in row}
    ordered = [key for key in preferred if key in keys]
    return ordered + sorted(keys - set(ordered))


def _node_name(node: dict[str, Any]) -> str:
    properties = node.get("properties") or {}
    return _cell(properties.get("name"))


def _node_labels(node: dict[str, Any]) -> str:
    return "|".join(str(label) for label in node.get("labels") or [])


def _write_csv(path: Path, columns: list[str], rows: list[list[str]]) -> None:
    with open(path, "w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(columns)
        writer.writerows(rows)


def write_nodes_csv(path: Path, nodes: list[dict[str, Any]]) -> None:
    properties = [node.get("properties") or {} for node in nodes]
    property_columns = _columns(properties, PREFERRED_PROPERTY_COLUMNS)
    columns = list(NODE_FIXED_COLUMNS) + property_columns
    rows = [
        [str(node["id"]), _node_labels(node)]
        + [_cell((node.get("properties") or {}).get(key)) for key in property_columns]
        for node in nodes
    ]
    _write_csv(path, columns, rows)


def write_relationships_csv(path: Path, relationships: list[dict[str, Any]],
                            nodes_by_id: dict[str, dict[str, Any]]) -> None:
    properties = [rel.get("properties") or {} for rel in relationships]
    raw_property_columns = _columns(properties)
    # 关系属性列与固定列重名时加前缀, 避免覆盖端点信息.
    property_columns = [
        f"rel_{key}" if key in REL_FIXED_COLUMNS else key
        for key in raw_property_columns
    ]
    columns = list(REL_FIXED_COLUMNS) + property_columns
    rows = []
    for rel in relationships:
        start = nodes_by_id.get(str(rel["start"]), {})
        end = nodes_by_id.get(str(rel["end"]), {})
        props = rel.get("properties") or {}
        row = [
            str(rel["id"]), str(rel["type"]),
            str(rel["start"]), _node_labels(start), _node_name(start),
            str(rel["end"]), _node_labels(end), _node_name(end),
        ]
        row.extend(_cell(props.get(key)) for key in raw_property_columns)
        rows.append(row)
    _write_csv(path, columns, rows)


def _xml_value(value: Any) -> str:
    return _cell(value)


def write_graphml(path: Path, payload: dict[str, Any]) -> None:
    nodes = payload["nodes"]
    relationships = payload["relationships"]
    node_property_columns = _columns(
        [node.get("properties") or {} for node in nodes],
        PREFERRED_PROPERTY_COLUMNS)
    rel_property_columns = _columns(
        [rel.get("properties") or {} for rel in relationships])

    ns = "http://graphml.graphdrawing.org/xmlns"
    ET.register_namespace("", ns)
    root = ET.Element(f"{{{ns}}}graphml")
    key_ids: dict[tuple[str, str], str] = {}

    def add_key(scope: str, name: str) -> str:
        key = (scope, name)
        if key in key_ids:
            return key_ids[key]
        key_id = f"{scope}_{len(key_ids)}"
        ET.SubElement(root, f"{{{ns}}}key", {
            "id": key_id,
            "for": "node" if scope == "n" else "edge",
            "attr.name": name,
            "attr.type": "string",
        })
        key_ids[key] = key_id
        return key_id

    add_key("n", "labels")
    for name in node_property_columns:
        add_key("n", name)
    add_key("e", "type")
    for name in rel_property_columns:
        add_key("e", name)

    graph = ET.SubElement(root, f"{{{ns}}}graph", {
        "id": "G", "edgedefault": "directed"})
    node_tags: dict[str, str] = {}
    for index, node in enumerate(nodes):
        node_id = f"n{index}"
        node_tags[str(node["id"])] = node_id
        element = ET.SubElement(graph, f"{{{ns}}}node", {"id": node_id})
        ET.SubElement(element, f"{{{ns}}}data", {
            "key": key_ids[("n", "labels")]}).text = _node_labels(node)
        for name in node_property_columns:
            ET.SubElement(element, f"{{{ns}}}data", {
                "key": key_ids[("n", name)]}).text = _xml_value(
                    (node.get("properties") or {}).get(name))

    for index, rel in enumerate(relationships):
        element = ET.SubElement(graph, f"{{{ns}}}edge", {
            "id": f"e{index}",
            "source": node_tags[str(rel["start"])],
            "target": node_tags[str(rel["end"])],
        })
        ET.SubElement(element, f"{{{ns}}}data", {
            "key": key_ids[("e", "type")]}).text = str(rel["type"])
        for name in rel_property_columns:
            ET.SubElement(element, f"{{{ns}}}data", {
                "key": key_ids[("e", name)]}).text = _xml_value(
                    (rel.get("properties") or {}).get(name))

    ET.indent(root)
    ET.ElementTree(root).write(path, encoding="utf-8", xml_declaration=True)


def _cypher_ident(name: str) -> str:
    return "`" + str(name).replace("`", "``") + "`"


def _cypher_value(value: Any) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return json.dumps(value)
    if isinstance(value, (dict, list, tuple)):
        return json.dumps(value, ensure_ascii=False, sort_keys=True)
    return json.dumps(str(value), ensure_ascii=False)


def _cypher_map(properties: dict[str, Any]) -> str:
    entries = [
        f"{_cypher_ident(key)}: {_cypher_value(value)}"
        for key, value in properties.items()
    ]
    return "{" + ", ".join(entries) + "}"


def write_cypher(path: Path, payload: dict[str, Any]) -> None:
    nodes = payload["nodes"]
    relationships = payload["relationships"]
    source = payload.get("source") or {}
    lines = [
        "// Generated from a cop-test Neo4j export; run against an empty database.",
        f"// source: {source.get('path', '')}",
        f"// source sha256: {source.get('sha256', '')}",
        f"// nodes: {len(nodes)}, relationships: {len(relationships)}",
        "",
    ]
    node_tags: dict[str, str] = {}
    for index, node in enumerate(nodes):
        tag = f"n{index}"
        node_tags[str(node["id"])] = tag
        labels = "".join(
            ":" + _cypher_ident(label) for label in node.get("labels") or [])
        properties = dict(node.get("properties") or {})
        properties = {"neo4j_id": node["id"], **properties}
        lines.append(f"CREATE ({tag}{labels} {_cypher_map(properties)});")
    lines.append("")
    for rel in relationships:
        rel_type = _cypher_ident(rel["type"])
        start = node_tags[str(rel["start"])]
        end = node_tags[str(rel["end"])]
        properties = rel.get("properties") or {}
        suffix = f" {_cypher_map(properties)}" if properties else ""
        lines.append(f"CREATE ({start})-[:{rel_type}{suffix}]->({end});")
    lines.append("")
    path.write_text("\n".join(lines), encoding="utf-8")


def write_summary(path: Path, payload: dict[str, Any], out_dir: Path) -> None:
    nodes = payload["nodes"]
    relationships = payload["relationships"]
    source = payload.get("source") or {}
    extractor = payload.get("extractor") or {}
    labels = Counter(
        label for node in nodes for label in node.get("labels") or [])
    rel_types = Counter(str(rel["type"]) for rel in relationships)
    lines = [
        "# Neo4j 导出数据（可读格式）",
        "",
        f"- 源导出 JSON：`{payload.get('_input_path', '')}`",
        f"- 原始 dump：`{source.get('path', '')}`",
        f"- dump SHA-256：`{source.get('sha256', '')}`",
        f"- 导出时间：`{payload.get('exported_at', '')}`",
        f"- 抽取器：`{extractor.get('id', '')}` / `{extractor.get('version', '')}`",
        f"- 节点：**{len(nodes)}**；关系：**{len(relationships)}**",
        "",
        "## 文件",
        "",
        "| 文件 | 用途 |",
        "|---|---|",
        "| `nodes.csv` | 节点表，含标签、名称和全部属性；UTF-8 BOM，可直接用 Excel 打开 |",
        "| `relationships.csv` | 关系表，含关系类型和两端节点的标签/名称 |",
        "| `graph.graphml` | 标准图格式，可导入 Gephi / yEd / Cytoscape |",
        "| `graph.cypher` | 可读的 Cypher `CREATE` 语句，可在空 Neo4j 库中重放 |",
        "",
        "## 标签分布",
        "",
        "| 标签 | 数量 |",
        "|---|---|",
    ]
    lines.extend(f"| `{label}` | {count} |" for label, count in labels.most_common())
    lines.extend([
        "",
        "## 关系类型分布",
        "",
        "| 关系类型 | 数量 |",
        "|---|---|",
    ])
    lines.extend(
        f"| `{rel_type}` | {count} |"
        for rel_type, count in rel_types.most_common())
    lines.extend([
        "",
        "## 生成命令",
        "",
        "```powershell",
        "uv run python tools/neo4j_export_formats.py `",
        f"  --export {payload.get('_input_path', '')} `",
        f"  --out-dir {out_dir}",
        "```",
        "",
    ])
    path.write_text("\n".join(lines), encoding="utf-8")


def parse_args(argv=None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="把 Neo4j 导出 JSON 转成 CSV / GraphML / Cypher")
    parser.add_argument("--export", required=True,
                        help="tools/neo4j_export.py 生成的 JSON")
    parser.add_argument("--out-dir", default="var/neo4j_export",
                        help="输出目录 (默认 var/neo4j_export)")
    return parser.parse_args(argv)


def main(argv=None) -> int:
    args = parse_args(argv)
    export_path = Path(args.export)
    if not export_path.is_file():
        print(f"[ERROR] 导出 JSON 不存在: {export_path}", file=sys.stderr)
        return 2
    try:
        payload = json.loads(export_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        print(f"[ERROR] 无法读取导出 JSON: {exc}", file=sys.stderr)
        return 2
    if payload.get("format") != EXPORT_FORMAT:
        print(f"[ERROR] 不支持的格式: {payload.get('format')!r} "
              f"(期望 {EXPORT_FORMAT!r})", file=sys.stderr)
        return 2
    nodes = payload.get("nodes")
    relationships = payload.get("relationships")
    if not isinstance(nodes, list) or not isinstance(relationships, list):
        print("[ERROR] 导出 JSON 缺少 nodes/relationships 数组", file=sys.stderr)
        return 2

    nodes_by_id: dict[str, dict[str, Any]] = {}
    for node in nodes:
        node_id = str(node.get("id", ""))
        if not node_id or node_id in nodes_by_id:
            print(f"[ERROR] 节点 id 缺失或重复: {node_id!r}", file=sys.stderr)
            return 2
        nodes_by_id[node_id] = node
    for rel in relationships:
        if str(rel.get("start")) not in nodes_by_id or str(rel.get("end")) not in nodes_by_id:
            print(f"[ERROR] 关系端点缺失: {rel.get('id')!r}", file=sys.stderr)
            return 2
        for key in ("id", "type", "start", "end"):
            if not str(rel.get(key, "")).strip():
                print(f"[ERROR] 关系字段缺失: {key} in {rel!r}", file=sys.stderr)
                return 2

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    write_nodes_csv(out_dir / "nodes.csv", nodes)
    write_relationships_csv(
        out_dir / "relationships.csv", relationships, nodes_by_id)
    write_graphml(out_dir / "graph.graphml", payload)
    write_cypher(out_dir / "graph.cypher", payload)
    payload["_input_path"] = str(export_path)
    write_summary(out_dir / "README.md", payload, out_dir)

    print(json.dumps({
        "ok": True,
        "out_dir": str(out_dir),
        "nodes": len(nodes),
        "relationships": len(relationships),
        "files": ["nodes.csv", "relationships.csv", "graph.graphml",
                  "graph.cypher", "README.md"],
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())