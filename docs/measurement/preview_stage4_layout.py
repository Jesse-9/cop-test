"""preview_stage4_layout.py — 从 Stage4Layout 配置生成单张俯视预览 (SVG).

用法:
    .venv/Scripts/python docs/measurement/preview_stage4_layout.py
    .venv/Scripts/python docs/measurement/preview_stage4_layout.py \
        --layout candidate.json --out docs/implementation/evidence/preview.svg

预览只用于检查空间关系, 不是碰撞/可达性证据. 内容来自 `layout.as_dict()`
同一份配置, 不单独维护坐标表; 图中区分:
  - 设备占地 (岛/置物台/仓库/传送带外框);
  - 固定输送方向与 A→C 交接点;
  - AGV 直道、转向节点与转向扫掠圆;
  - 四个 dock 与 E 的两个输入缓冲/成品输出;
  - 高度 datum 文字 (Z_base / Z_work / Z_belt / H_transfer).
"""

from __future__ import annotations

import argparse
import html
import json
import math
import sys
from pathlib import Path

_SRC = Path(__file__).resolve().parents[2] / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from stage1 import config as base
from stage4 import config as cfg
from stage4.layout import (agv_turn_radius, default_layout, layout_from_dict,
                           line_specs)

W = 1600
H = 1100
PLOT = (70, 125, 1100, 880)  # x, y, width, height
SIDEBAR_X = 1210


def _load_layout(path: str | None):
    if not path:
        return default_layout()
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    return layout_from_dict(payload.get("layout", payload))


class Canvas:
    def __init__(self, layout) -> None:
        floor = layout.floor_rect
        self.floor = floor
        inset = 45
        self.scale = min((PLOT[2] - 2 * inset) / (floor[2] - floor[0]),
                         (PLOT[3] - 2 * inset) / (floor[3] - floor[1]))
        self.ox = floor[0]
        self.oy = floor[1]
        self.height = floor[3] - floor[1]
        self.plot_x = PLOT[0] + inset
        self.plot_y = PLOT[1] + inset
        self.parts: list[str] = []

    def px(self, x: float, y: float) -> tuple[float, float]:
        return (self.plot_x + (x - self.ox) * self.scale,
                self.plot_y + (self.height - (y - self.oy)) * self.scale)

    def rect(self, rect, *, fill="none", stroke="#333", width=1.0,
             dash=None, opacity=1.0, label="", label_size=11,
             label_color="#111") -> None:
        x0, y0 = self.px(rect[0], rect[3])
        x1, y1 = self.px(rect[2], rect[1])
        dash_attr = f' stroke-dasharray="{dash}"' if dash else ""
        self.parts.append(
            f'<rect x="{x0:.2f}" y="{y0:.2f}" width="{x1 - x0:.2f}" '
            f'height="{y1 - y0:.2f}" fill="{fill}" fill-opacity="{opacity}" '
            f'stroke="{stroke}" stroke-width="{width}"{dash_attr}/>')
        if label:
            cx = (x0 + x1) / 2
            cy = (y0 + y1) / 2
            self.parts.append(
                f'<text x="{cx:.2f}" y="{cy:.2f}" font-size="{label_size}" '
                f'text-anchor="middle" dominant-baseline="middle" '
                f'fill="{label_color}">{label}</text>')

    def line(self, a, b, *, stroke="#666", width=2.0, dash=None,
             label="", label_size=10, label_color="#444",
             arrow=True) -> None:
        x0, y0 = self.px(a[0], a[1])
        x1, y1 = self.px(b[0], b[1])
        dash_attr = f' stroke-dasharray="{dash}"' if dash else ""
        marker = ' marker-end="url(#arrow)"' if arrow else ""
        self.parts.append(
            f'<line x1="{x0:.2f}" y1="{y0:.2f}" x2="{x1:.2f}" '
            f'y2="{y1:.2f}" stroke="{stroke}" stroke-width="{width}"'
            f'{dash_attr}{marker}/>')
        if label:
            mx, my = (x0 + x1) / 2, (y0 + y1) / 2
            self.parts.append(
                f'<text x="{mx:.2f}" y="{my - 5:.2f}" font-size="{label_size}" '
                f'text-anchor="middle" fill="{label_color}">{label}</text>')

    def circle(self, center, radius, *, stroke="#999", width=1.0,
               dash="4,4", fill="none") -> None:
        cx, cy = self.px(center[0], center[1])
        r = radius * self.scale
        dash_attr = f' stroke-dasharray="{dash}"' if dash else ""
        self.parts.append(
            f'<circle cx="{cx:.2f}" cy="{cy:.2f}" r="{r:.2f}" '
            f'fill="{fill}" stroke="{stroke}" stroke-width="{width}"'
            f'{dash_attr}/>')

    def point(self, pos, *, color="#c00", radius=3.0, label="",
              label_size=11, label_color="#900") -> None:
        cx, cy = self.px(pos[0], pos[1])
        self.parts.append(
            f'<circle cx="{cx:.2f}" cy="{cy:.2f}" r="{radius}" '
            f'fill="{color}"/>')
        if label:
            self.parts.append(
                f'<text x="{cx + 6:.2f}" y="{cy - 6:.2f}" '
                f'font-size="{label_size}" fill="{label_color}">{label}</text>')

    def text(self, x: float, y: float, text: str, *, size=11,
             color="#111", anchor="start", weight="normal") -> None:
        px, py = self.px(x, y)
        self.parts.append(
            f'<text x="{px:.2f}" y="{py:.2f}" font-size="{size}" '
            f'text-anchor="{anchor}" fill="{color}" '
            f'font-weight="{weight}">{text}</text>')

    def render(self, layout, language: str, notes: list[str], legend: list[tuple[str, str]]) -> str:
        zh = language == "zh"
        title = (f"阶段 4.1｜布局 {layout.name}｜俯视图" if zh
                 else f"Stage 4.1 | Layout {layout.name} | Top View")
        subtitle = ("五岛工作单元、固定物流与外围 AGV 路网" if zh
                    else "Five work cells, fixed conveyors and perimeter AGV routes")
        font = "'SimSun', '宋体', serif" if zh else "'Times New Roman', Times, serif"
        esc = html.escape
        head = (
            f'<svg xmlns="http://www.w3.org/2000/svg" width="{W}" '
            f'height="{H}" viewBox="0 0 {W} {H}" lang="{language}">'
            '<defs><marker id="arrow" viewBox="0 0 10 10" refX="9" '
            'refY="5" markerWidth="6" markerHeight="6" orient="auto">'
            '<path d="M 0 0 L 10 5 L 0 10 z" fill="#526477"/></marker></defs>'
            f'<rect width="{W}" height="{H}" fill="#f7f8fa"/>')
        title_svg = (
            f'<text x="70" y="48" font-family="{font}" font-size="28" '
            f'font-weight="bold" fill="#182433">{esc(title)}</text>'
            f'<text x="70" y="82" font-family="{font}" font-size="17" '
            f'fill="#526477">{esc(subtitle)}</text>')
        body = "".join(self.parts)
        sidebar = [
            f'<rect x="1165" y="120" width="365" height="900" rx="4" '
            'fill="#ffffff" stroke="#d3dbe4" stroke-width="1.5"/>',
            f'<text x="1195" y="158" font-family="{font}" font-size="21" '
            f'font-weight="bold" fill="#182433">{("图例与布局说明" if zh else "LEGEND &amp; LAYOUT")}</text>',
        ]
        y = 200
        for heading, entries in legend:
            sidebar.append(
                f'<text x="1195" y="{y}" font-family="{font}" font-size="16" '
                f'font-weight="bold" fill="#26384a">{esc(heading)}</text>')
            y += 27
            for entry in entries:
                sidebar.append(
                    f'<text x="1195" y="{y}" font-family="{font}" font-size="14" '
                    f'fill="#354556">{esc(entry)}</text>')
                y += 24
            y += 12
        y += 2
        sidebar.append(
            f'<line x1="1195" y1="{y}" x2="1500" y2="{y}" '
            'stroke="#d3dbe4" stroke-width="1"/>')
        y += 28
        sidebar.append(
            f'<text x="1195" y="{y}" font-family="{font}" font-size="14" '
            f'font-weight="bold" fill="#26384a">{("高度基准" if zh else "HEIGHT DATUM")}</text>')
        y += 24
        for note in notes:
            sidebar.append(
                f'<text x="1195" y="{y}" font-family="{font}" font-size="13" '
                f'fill="#354556">{esc(note)}</text>')
            y += 22
        footer = ("本图用于查看空间关系；不代表 IK 或碰撞验证结果。" if zh
                  else "Spatial overview only; not IK or collision evidence.")
        sidebar.append(
            f'<text x="70" y="1065" font-family="{font}" font-size="14" '
            f'fill="#526477">{esc(footer)}</text>')
        return head + title_svg + body + "".join(sidebar) + "</svg>"


def build_svg(layout, language: str = "en") -> str:
    zh = language == "zh"
    canvas = Canvas(layout)
    floor = layout.floor_rect
    canvas.rect(floor, fill="#f1f4f6", stroke="#aab6c2", width=1.5)

    # 设备
    for spec in layout.units:
        canvas.rect(spec.island_rect, fill="#dce6ef", stroke="#536b80", width=1.5)
        if spec.has_station:
            canvas.rect(spec.station_rect, fill="#f4e7d3", stroke="#9b7952",
                        width=1.3)
        if spec.dock_rect is not None:
            canvas.rect(spec.dock_rect, fill="#fff0cb", stroke="#bd8424",
                        width=1.5, dash="5,3")

    canvas.rect(layout.warehouse_rect, fill="#e1eee4", stroke="#527b5b", width=1.3)
    canvas.rect(layout.warehouse_s2_rect, fill="#e1eee4", stroke="#527b5b", width=1.3)

    lines = layout.lines
    for line_id, line in lines.items():
        canvas.rect(line.rect, fill="#ddeef2", stroke="#2a7", width=1.6)
        canvas.line(line.entry, line.stop, stroke="#167b89", width=2.5)
        canvas.point(line.entry, color="#167b89", radius=3)
        canvas.point(line.stop, color="#167b89", radius=3)

    # 固定交接 / E 缓冲 / 输出
    handoff = layout.handoff_ac
    canvas.point(handoff, color="#9a5793", radius=5)
    for pos in layout.buffers.values():
        canvas.point(pos, color="#c27a28", radius=4)
    canvas.rect(layout.e_output_table_rect, fill="#eeeae0", stroke="#786d59", width=1.3)
    canvas.point(layout.e_output, color="#56864a", radius=4)

    # AGV 路网
    for edge in layout.agv_edges:
        a = layout.agv_nodes[edge.start]
        b = layout.agv_nodes[edge.end]
        canvas.line(a, b, stroke="#bc832c", width=3.5, dash="9,5")
    radius = agv_turn_radius()
    for node in layout.agv_turn_nodes:
        center = layout.agv_nodes[node]
        canvas.circle(center, radius, stroke="#c60", width=1.2)
        canvas.point(center, color="#c60", radius=3.5)
    canvas.point(layout.agv_nodes[layout.agv_initial_dock],
                 color="#b34747", radius=5)

    datum = layout.height_datum
    if zh:
        legend = [
            ("设备与工位", ["蓝灰块：工作岛 A–E", "浅棕块：工位台 A/B", "虚线黄框：AGV 停靠点 A/B/D/E", "浅绿块：原料仓 S1/S2"]),
            ("固定物流与物料点", ["青色线：传送带 S1→A、S1→B、S2→D、C→E", "紫色点：A→C 交接点", "橙色点：E 岛缓冲区 C/D", "绿色点：成品输出；灰色块：输出台"]),
            ("AGV 路网", ["橙色虚线：外围 U 形路网（西/南/东）", "橙色圆点：转向节点 J_SW/J_SE", "红色点：AGV 初始停靠位置 A", "箭头表示传送带方向及路段方向"]),
        ]
        notes = [
            f"基座 Z_base：{datum.get('Z_base')} m",
            f"带面 Z_belt：{datum.get('Z_belt')} m",
            f"转运高度 H_transfer：{datum.get('H_transfer')} m",
            "工作面 Z_work：A/B/E=0.30 m；C/D=0.45 m",
        ]
    else:
        legend = [
            ("Equipment & cells", ["Blue-gray blocks: islands A-E", "Sand blocks: stations A/B", "Dashed amber: AGV docks A/B/D/E", "Green blocks: warehouses S1/S2"]),
            ("Conveyors & material points", ["Teal lines: S1→A, S1→B, S2→D, C→E", "Purple point: A→C handoff", "Orange points: E buffers C/D", "Green point: finished output; gray block: output table"]),
            ("AGV network", ["Dashed amber: perimeter U-shaped lanes", "Orange nodes: turns J_SW/J_SE", "Red point: AGV initial dock A", "Arrows show conveyor and lane directions"]),
        ]
        notes = [
            f"Base Z_base: {datum.get('Z_base')} m",
            f"Belt Z_belt: {datum.get('Z_belt')} m",
            f"Transfer height H_transfer: {datum.get('H_transfer')} m",
            "Work surfaces Z_work: A/B/E=0.30 m; C/D=0.45 m",
        ]
    return canvas.render(layout, language, notes, legend)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="阶段四布局俯视预览 (SVG)")
    ap.add_argument("--layout", default=None,
                    help="候选布局 JSON (layout.as_dict())")
    ap.add_argument("--out", default=None, help="输出 SVG 路径")
    ap.add_argument("--lang", choices=("en", "zh"), default="zh",
                    help="输出语言（英文或中文）")
    ns = ap.parse_args(argv)
    layout = _load_layout(ns.layout)
    svg = build_svg(layout, ns.lang)
    default_name = (f"stage4_layout_{layout.name}_preview.svg" if ns.lang == "zh"
                    else f"stage4_layout_{layout.name}_preview_en.svg")
    out = Path(ns.out) if ns.out else Path(
        "docs", "implementation", "evidence", default_name)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(svg, encoding="utf-8")
    print(f"[OK] 写入 {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
