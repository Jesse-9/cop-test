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

W = 1200
H = 900
MARGIN = 70


def _load_layout(path: str | None):
    if not path:
        return default_layout()
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    return layout_from_dict(payload.get("layout", payload))


class Canvas:
    def __init__(self, layout) -> None:
        floor = layout.floor_rect
        self.floor = floor
        self.scale = min((W - 2 * MARGIN) / (floor[2] - floor[0]),
                         (H - 2 * MARGIN) / (floor[3] - floor[1]))
        self.ox = floor[0]
        self.oy = floor[1]
        self.height = floor[3] - floor[1]
        self.parts: list[str] = []

    def px(self, x: float, y: float) -> tuple[float, float]:
        return (MARGIN + (x - self.ox) * self.scale,
                MARGIN + (self.height - (y - self.oy)) * self.scale)

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

    def render(self, title: str, notes: list[str]) -> str:
        head = (
            f'<svg xmlns="http://www.w3.org/2000/svg" width="{W}" '
            f'height="{H}" viewBox="0 0 {W} {H}">'
            '<defs><marker id="arrow" viewBox="0 0 10 10" refX="9" '
            'refY="5" markerWidth="6" markerHeight="6" orient="auto">'
            '<path d="M 0 0 L 10 5 L 0 10 z" fill="#666"/></marker></defs>'
            f'<rect width="{W}" height="{H}" fill="#fbfbf7"/>')
        title_svg = (f'<text x="{MARGIN}" y="34" font-size="20" '
                     f'font-weight="bold" fill="#111">{title}</text>')
        body = "".join(self.parts)
        note_svg = "".join(
            f'<text x="{W - MARGIN}" y="{H - 20 - i * 16}" font-size="11" '
            f'text-anchor="end" fill="#333">{line}</text>'
            for i, line in enumerate(reversed(notes)))
        return head + title_svg + body + note_svg + "</svg>"


def build_svg(layout) -> str:
    canvas = Canvas(layout)
    floor = layout.floor_rect
    canvas.rect(floor, fill="#f0efe8", stroke="#aaa", label="Floor")

    # 设备
    for spec in layout.units:
        canvas.rect(spec.island_rect, fill="#cfd8e3", stroke="#445",
                    label=f"Island {spec.unit_id}")
        if spec.unit_id == "A":
            canvas.text(spec.island_pos[0], spec.island_pos[1] + 0.42,
                        f"base ({spec.arm_base[0]:.2f}, "
                        f"{spec.arm_base[1]:.2f}, {spec.arm_base[2]:.2f})",
                        size=9, color="#223", anchor="middle")
        if spec.has_station:
            canvas.rect(spec.station_rect, fill="#e8dcc8", stroke="#665",
                        label=f"Stn {spec.unit_id}")
        if spec.dock_rect is not None:
            canvas.rect(spec.dock_rect, fill="#ffe9b8", stroke="#b80",
                        dash="5,3", label=f"dock {spec.unit_id}")

    canvas.rect(layout.warehouse_rect, fill="#d8e8d8", stroke="#365",
                label="WH S1")
    canvas.rect(layout.warehouse_s2_rect, fill="#d8e8d8", stroke="#365",
                label="WH S2")

    lines = layout.lines
    for line_id, line in lines.items():
        canvas.rect(line.rect, fill="#ddeef2", stroke="#2a7", width=1.6)
        canvas.line(line.entry, line.stop, stroke="#178", width=2.0,
                    label=line_id, label_size=9, label_color="#145")
        canvas.point(line.entry, color="#178", radius=2.5,
                     label="entry", label_size=8, label_color="#145")
        canvas.point(line.stop, color="#178", radius=2.5,
                     label="stop", label_size=8, label_color="#145")

    # 固定交接 / E 缓冲 / 输出
    handoff = layout.handoff_ac
    canvas.point(handoff, color="#a0a", radius=5, label="A→C handoff")
    for key, pos in layout.buffers.items():
        canvas.point(pos, color="#960", radius=4, label=key)
    canvas.rect(layout.e_output_table_rect, fill="#e8e4d8", stroke="#665",
                label="E output table")
    canvas.point(layout.e_output, color="#690", radius=4, label="E output")

    # AGV 路网
    for edge in layout.agv_edges:
        a = layout.agv_nodes[edge.start]
        b = layout.agv_nodes[edge.end]
        canvas.line(a, b, stroke="#995", width=3.0, dash="9,5",
                    label=edge.lane.replace("agv_lane_", ""),
                    label_color="#663")
    radius = agv_turn_radius()
    for node in layout.agv_turn_nodes:
        center = layout.agv_nodes[node]
        canvas.circle(center, radius, stroke="#c60", width=1.2)
        canvas.point(center, color="#c60", radius=3.5, label=node,
                     label_color="#840")
    canvas.point(layout.agv_nodes[layout.agv_initial_dock],
                 color="#b00", radius=5,
                 label=f"AGV start {layout.agv_initial_dock}")

    datum = layout.height_datum
    notes = [
        f"Z_base={datum.get('Z_base')} m;  Z_belt={datum.get('Z_belt')} m;  "
        f"H_transfer={datum.get('H_transfer')} m",
        "Z_work: " + ", ".join(f"{key}={value}"
                               for key, value in
                               (datum.get("Z_work") or {}).items()),
        "preview 只检查空间关系, 不是 IK/碰撞证据; 数据来自 "
        f"{layout.name} 配置",
    ]
    title = (f"Stage 4.1 layout {layout.name} — top view "
             f"(AGV docks non-collinear, U-shaped fixed flow)")
    return canvas.render(title, notes)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="阶段四布局俯视预览 (SVG)")
    ap.add_argument("--layout", default=None,
                    help="候选布局 JSON (layout.as_dict())")
    ap.add_argument("--out", default=None, help="输出 SVG 路径")
    ns = ap.parse_args(argv)
    layout = _load_layout(ns.layout)
    svg = build_svg(layout)
    out = Path(ns.out) if ns.out else Path(
        "docs", "implementation", "evidence",
        f"stage4_layout_{layout.name}_preview.svg")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(svg, encoding="utf-8")
    print(f"[OK] 写入 {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
