"""persistence.diff — 代码目录与数据库目录的稳定序列化差异.

比较使用 ``ProcessCatalog.as_dict()``: 映射键排序无语义, 列表顺序 (recipes,
operations, routes, inputs, work steps, resources, candidates) 保留. 返回
逐路径差异, 供影子读取阶段定位真实数据差异, 不用放宽比较掩盖顺序问题.
"""

from __future__ import annotations

from typing import Any, Optional

from ..domain.process_model import ProcessCatalog


def _diff(path: str, expected: Any, actual: Any,
          out: list[dict]) -> None:
    if type(expected) is not type(actual):
        out.append({"path": path, "expected": expected, "actual": actual})
        return
    if isinstance(expected, dict):
        for key in sorted(set(expected) | set(actual)):
            child = f"{path}.{key}" if path else str(key)
            if key not in expected or key not in actual:
                out.append({"path": child,
                            "expected": expected.get(key, "<missing>"),
                            "actual": actual.get(key, "<missing>")})
            else:
                _diff(child, expected[key], actual[key], out)
        return
    if isinstance(expected, list):
        if len(expected) != len(actual):
            out.append({"path": f"{path}.length",
                        "expected": len(expected),
                        "actual": len(actual)})
        for index in range(min(len(expected), len(actual))):
            _diff(f"{path}[{index}]", expected[index], actual[index], out)
        return
    if expected != actual:
        out.append({"path": path, "expected": expected, "actual": actual})


def catalog_diff(expected: ProcessCatalog, actual: ProcessCatalog) -> list[dict]:
    differences: list[dict] = []
    _diff("", expected.as_dict(), actual.as_dict(), differences)
    return differences


def shadow_report(code_catalog: ProcessCatalog,
                  database_catalog: Optional[ProcessCatalog],
                  *, error: str = "") -> dict:
    if database_catalog is None:
        return {"ok": False, "differences": [], "error": error,
                "code_version": code_catalog.version}
    differences = catalog_diff(code_catalog, database_catalog)
    return {
        "ok": not differences,
        "differences": differences,
        "error": "",
        "code_version": code_catalog.version,
        "database_version": database_catalog.version,
    }
