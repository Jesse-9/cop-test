import { afterEach, describe, expect, it, vi } from "vitest";
import { api, ApiError } from "./client";
import type { OrderSpec, TemplateCatalog } from "./types";

const catalog: TemplateCatalog = {
  catalog_version: "v1_process_test",
  contract_version: "v1_order_test",
  scene_id: "L5_UFLOW",
  templates: [],
  routes: [],
};

function jsonResponse(payload: unknown, status = 200): Response {
  return new Response(JSON.stringify(payload), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("order API client", () => {
  it("loads templates from the configured API base", async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      jsonResponse({ ok: true, ...catalog }),
    );
    vi.stubGlobal("fetch", fetchMock);

    const result = await api.templates();

    expect(result.catalog_version).toBe("v1_process_test");
    expect(fetchMock).toHaveBeenCalledWith(
      "/api/templates",
      expect.objectContaining({ method: "GET" }),
    );
  });

  it("maps 422 diagnostics into ApiError", async () => {
    const diagnostic = {
      code: "ORDER_PORT_UNSUPPLIED",
      message: "端口没有供给者",
      node_id: "node-1",
      input_id: "",
      edge_index: -1,
      port_id: "input-0",
    };
    vi.stubGlobal(
      "fetch",
      vi.fn(() =>
        Promise.resolve(
          jsonResponse(
            {
              ok: false,
              error: {
                code: "ORDER_VALIDATION",
                message: "订单结构校验失败",
                diagnostics: [diagnostic],
              },
            },
            422,
          ),
        ),
      ),
    );

    const spec = { order_id: "x" } as OrderSpec;
    await expect(api.preview(spec)).rejects.toMatchObject({
      status: 422,
      code: "ORDER_VALIDATION",
      diagnostics: [diagnostic],
    });
    await expect(api.preview(spec)).rejects.toBeInstanceOf(ApiError);
  });

  it("maps connection failures to NETWORK", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockRejectedValue(new TypeError("fetch failed")),
    );
    await expect(api.health()).rejects.toMatchObject({
      status: 0,
      code: "NETWORK",
    });
  });

  it("rejects non-JSON responses without inventing a success", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(new Response("not json", { status: 200 })),
    );
    await expect(api.templates()).rejects.toMatchObject({
      status: 200,
      code: "INVALID_RESPONSE",
    });
  });
});
