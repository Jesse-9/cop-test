"""api.server — 订单服务的本地 HTTP adapter (阶段 3).

HTTP 层只做 JSON 编解码、路由和错误映射; 工艺校验、版本不可变性、运行
生命周期全部在 :class:`~flexible_factory.application.orders.OrderService`.
handler 不持有 sim/scene/controller/handle, 也不直接调用 CoppeliaSim API.

默认只绑定 ``127.0.0.1``. 首期没有认证/多租户; 部署到非本机地址前必须先
增加访问控制和 TLS.
"""

from __future__ import annotations

import json
import logging
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, unquote, urlsplit

from ..application.orders import OrderService
from ..domain.orders import (ORDER_CONTRACT_VERSION, OrderSpecError,
                             OrderValidationError)
from ..persistence.errors import (OrderIntegrityError, OrderNotFoundError,
                                  OrderRunConflictError, OrderRunNotFoundError,
                                  OrderStateError,
                                  OrderVersionConflictError,
                                  OrderVersionNotFoundError)

LOGGER = logging.getLogger("flexible_factory.api")
MAX_BODY_BYTES = 1024 * 1024

_NOT_FOUND_ERRORS = (OrderNotFoundError, OrderVersionNotFoundError,
                     OrderRunNotFoundError)
_CONFLICT_ERRORS = (OrderVersionConflictError, OrderRunConflictError,
                    OrderStateError)


class ApiError(RuntimeError):
    def __init__(self, status: int, code: str, message: str,
                 *, path: str = "", diagnostics=None) -> None:
        super().__init__(message)
        self.status = int(status)
        self.code = str(code)
        self.path = str(path)
        self.diagnostics = list(diagnostics or [])


class OrderApiHandler(BaseHTTPRequestHandler):
    service: OrderService
    server_version = "flexible-factory-order-api/1.0"

    # ── 路由 ────────────────────────────────────────────────

    def do_GET(self) -> None:
        self._handle("GET")

    def do_POST(self) -> None:
        self._handle("POST")

    def _handle(self, method: str) -> None:
        try:
            payload = self._dispatch(method)
            self._json(200, {"ok": True, **payload})
        except ApiError as exc:
            self._json(exc.status, {
                "ok": False,
                "error": {
                    "code": exc.code,
                    "message": str(exc),
                    "path": exc.path,
                    "diagnostics": exc.diagnostics,
                },
            })
        except OrderSpecError as exc:
            self._json(422, _domain_error("ORDER_SPEC", exc, path=exc.path))
        except OrderValidationError as exc:
            self._json(422, _domain_error(
                "ORDER_VALIDATION", exc, diagnostics=[
                    row.as_dict() for row in exc.diagnostics]))
        except _NOT_FOUND_ERRORS as exc:
            self._json(404, _domain_error(getattr(exc, "code", "NOT_FOUND"),
                                          exc))
        except _CONFLICT_ERRORS as exc:
            self._json(409, _domain_error(getattr(exc, "code", "CONFLICT"),
                                          exc))
        except OrderIntegrityError as exc:
            self._json(500, _domain_error("ORDER_INTEGRITY", exc))
        except Exception as exc:
            LOGGER.exception("[API] 未处理异常")
            self._json(500, _domain_error(
                "INTERNAL", exc, message=f"{type(exc).__name__}: {exc}"))

    def _dispatch(self, method: str) -> dict:
        split = urlsplit(self.path)
        parts = [unquote(part) for part in split.path.split("/") if part]
        if not parts or parts[0] != "api":
            raise ApiError(404, "NOT_FOUND", f"未知路径 {split.path!r}")
        query = parse_qs(split.query)
        parts = parts[1:]

        if parts == ["health"] and method == "GET":
            return self._health()
        if parts == ["templates"] and method == "GET":
            return self.service.templates()
        if parts == ["planner", "plan"] and method == "POST":
            plan = self._planner_call(
                lambda: self.service.plan(self._read_json()))
            return {"plan": plan.as_dict()}
        if parts == ["planner", "drafts"] and method == "POST":
            result = self._planner_call(
                lambda: self.service.plan_and_save(self._read_json()))
            if not result.get("ok"):
                diagnostics = (result.get("plan") or {}).get(
                    "diagnostics") or []
                raise ApiError(
                    422, "PLANNER_NO_SOLUTION",
                    "planner 未生成可行草稿",
                    diagnostics=diagnostics)
            return {"draft": result}
        if parts == ["orders"]:
            if method == "GET":
                limit = _int_query(query, "limit", 50)
                return {"orders": self.service.list_orders(limit=limit)}
            if method == "POST":
                body = self._read_json()
                return {"order": self.service.create_order(body)}
            raise self._method_not_allowed("GET, POST")
        if len(parts) == 2 and parts[0] == "orders" and method == "GET":
            return {"order": self.service.get_order(parts[1])}
        if len(parts) == 3 and parts[0] == "orders" \
                and parts[2] == "versions":
            order_id = parts[1]
            if method == "GET":
                return {"versions": self.service.list_versions(order_id)}
            if method == "POST":
                body = self._read_json()
                return {"order": self.service.create_version(order_id, body)}
            raise self._method_not_allowed("GET, POST")
        if len(parts) == 3 and parts[0] == "orders" \
                and parts[2] == "runs" and method == "GET":
            limit = _int_query(query, "limit", 50)
            return {"runs": self.service.list_runs(
                order_id=parts[1], limit=limit)}
        if len(parts) == 4 and parts[0] == "orders" \
                and parts[2] == "versions":
            return self._version_route(method, parts[1], parts[3], query)
        if len(parts) == 5 and parts[0] == "orders" \
                and parts[2] == "versions":
            return self._version_action(method, parts[1], parts[3], parts[4])
        if parts == ["orders", "preview"] and method == "POST":
            return {"preview": self.service.preview(
                self._read_json()).as_dict()}
        if len(parts) >= 2 and parts[0] == "runs":
            return self._run_route(method, parts[1], parts[2:], query)
        raise ApiError(404, "NOT_FOUND", f"未知路径 {split.path!r}")

    def _version_route(self, method: str, order_id: str, raw_version: str,
                       query: dict) -> dict:
        version = _parse_version(raw_version)
        if method == "GET":
            return {"version": self.service.get_version(order_id, version)}
        if method == "POST":
            # POST /versions/{version} 不是合法动作; 预览/启动在子路径.
            raise self._method_not_allowed("GET")
        raise self._method_not_allowed("GET")

    def _version_action(self, method: str, order_id: str,
                        raw_version: str, action: str) -> dict:
        version = _parse_version(raw_version)
        if method != "POST":
            raise self._method_not_allowed("POST")
        if action == "preview":
            order = self.service.repository.get_spec(order_id, version)
            return {"preview": self.service.preview_spec(order).as_dict()}
        if action == "start":
            body = self._read_json(optional=True)
            try:
                max_sim_time = float(body.get("max_sim_time") or 1200.0)
            except (TypeError, ValueError) as exc:
                raise ApiError(
                    400, "INVALID_MAX_SIM_TIME",
                    "max_sim_time 必须是正数") from exc
            if max_sim_time <= 0:
                raise ApiError(400, "INVALID_MAX_SIM_TIME",
                               "max_sim_time 必须是正数")
            result = self.service.start(
                order_id, version,
                artifact_id=str(body.get("artifact_id") or ""),
                max_sim_time=max_sim_time)
            return {"run": result}
        raise ApiError(404, "NOT_FOUND", f"未知订单动作 {action!r}")

    def _run_route(self, method: str, run_id: str, rest: list[str],
                   query: dict) -> dict:
        if not rest:
            if method != "GET":
                raise self._method_not_allowed("GET")
            return {"run": self.service.status(run_id)}
        if rest == ["nodes"] and method == "GET":
            return {"nodes": self.service.nodes(run_id)}
        if rest == ["events"] and method == "GET":
            limit = _int_query(query, "limit", 2000)
            return {"events": self.service.events(run_id, limit=limit)}
        if rest == ["stop"] and method == "POST":
            return {"run": self.service.stop(run_id)}
        raise ApiError(404, "NOT_FOUND", f"未知运行路径 {rest}")

    @staticmethod
    def _planner_call(callable_):
        """planner 模块延迟导入; 其输入/错误码统一映射为 422."""
        try:
            return callable_()
        except Exception as exc:
            code = str(getattr(exc, "code", ""))
            if code.startswith("PLANNER"):
                raise ApiError(422, code, str(exc)) from exc
            raise

    def _health(self) -> dict:
        try:
            from ..application.planner import PLANNER_POLICY_VERSION
        except Exception:      # 阶段 3 部署可独立于阶段 4 planner 模块
            PLANNER_POLICY_VERSION = ""
        return {
            "service": "order",
            "contract_version": ORDER_CONTRACT_VERSION,
            "planner_policy_version": PLANNER_POLICY_VERSION,
            "catalog_version": self.service.catalog.version,
            "scene_id": "L5_UFLOW",
        }

    @staticmethod
    def _method_not_allowed(allow: str) -> ApiError:
        return ApiError(405, "METHOD_NOT_ALLOWED",
                        f"允许的方法: {allow}")

    # ── JSON ────────────────────────────────────────────────

    def _read_json(self, *, optional: bool = False) -> dict:
        raw_length = self.headers.get("Content-Length", "0")
        try:
            length = int(raw_length)
        except ValueError as exc:
            raise ApiError(400, "BAD_CONTENT_LENGTH",
                           "Content-Length 非法") from exc
        if length <= 0:
            if optional:
                return {}
            raise ApiError(400, "EMPTY_BODY", "请求体不能为空")
        if length > MAX_BODY_BYTES:
            raise ApiError(413, "BODY_TOO_LARGE",
                           f"请求体超过 {MAX_BODY_BYTES} 字节")
        raw = self.rfile.read(length)
        try:
            payload = json.loads(raw.decode("utf-8"))
        except Exception as exc:
            raise ApiError(400, "INVALID_JSON",
                           f"JSON 解析失败: {exc}") from exc
        if not isinstance(payload, dict):
            raise ApiError(400, "INVALID_JSON", "请求体必须是 JSON 对象")
        return payload

    def _json(self, status: int, payload: dict) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(int(status))
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, fmt: str, *args) -> None:
        LOGGER.info("[API] %s - %s", self.address_string(), fmt % args)


def _domain_error(code: str, exc: Exception, *, path: str = "",
                  message: str = "", diagnostics=None) -> dict:
    return {
        "ok": False,
        "error": {
            "code": code,
            "message": message or str(exc),
            "path": path,
            "diagnostics": list(diagnostics or []),
        },
    }


def _parse_version(raw: str) -> int:
    try:
        value = int(raw)
    except ValueError as exc:
        raise ApiError(400, "INVALID_VERSION",
                       f"版本号非法: {raw!r}") from exc
    if value <= 0:
        raise ApiError(400, "INVALID_VERSION", "版本号必须 > 0")
    return value


def _int_query(query: dict, key: str, default: int) -> int:
    raw = (query.get(key) or [""])[0]
    if not raw:
        return int(default)
    try:
        value = int(raw)
    except ValueError as exc:
        raise ApiError(400, "INVALID_QUERY",
                       f"查询参数 {key} 必须是整数") from exc
    return max(1, value)


class OrderApiServer:
    """本地订单 HTTP 服务; handler 只引用 OrderService."""

    def __init__(self, service: OrderService, *, host: str = "127.0.0.1",
                 port: int = 8080) -> None:
        handler = type("BoundOrderApiHandler", (OrderApiHandler,),
                       {"service": service})
        self._httpd = ThreadingHTTPServer((str(host), int(port)), handler)
        self._httpd.daemon_threads = True
        self.host = str(host)
        self.port = int(self._httpd.server_address[1])

    @property
    def base_url(self) -> str:
        return f"http://{self.host}:{self.port}"

    def serve_forever(self) -> None:
        self._httpd.serve_forever()

    def shutdown(self) -> None:
        self._httpd.shutdown()
        self._httpd.server_close()

    def __enter__(self) -> "OrderApiServer":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.shutdown()


__all__ = ["ApiError", "OrderApiHandler", "OrderApiServer"]
