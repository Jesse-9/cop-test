import { afterEach, describe, expect, it, vi } from "vitest";
import { api, ApiError } from "./client";
import type { OrderSpec, TemplateCatalog } from "./types";

const catalog: TemplateCatalog = {
  catalog_version: "v1_process_test",
  contract_version: "v1_order_test",
  scene_id: "L5_UFLOW",
  artifact_id: "v1/L5_UFLOW/v005",
  scene_profile: "full_flow",
  profile_source: "manifest",
  profile_version: "v1_scene_profile_test",
  profiles: ["full_flow", "assembly_mvp"],
  item_types: { targets: ["FINAL"], inputs: ["RAW_A"] },
  zones: [
    {
      id: "line_s1a",
      capacity: 1,
      description: "S1→A 输入带",
      spawnable: true,
      item_types: ["RAW_A"],
    },
  ],
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
    expect(result.scene_profile).toBe("full_flow");
    expect(result.item_types.targets).toEqual(["FINAL"]);
    expect(fetchMock).toHaveBeenCalledWith(
      "/api/templates",
      expect.objectContaining({ method: "GET" }),
    );
  });

  it("queries the template catalog for a specific artifact", async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      jsonResponse({ ok: true, ...catalog }),
    );
    vi.stubGlobal("fetch", fetchMock);

    await api.templates("v1/L5_UFLOW/v005");

    expect(fetchMock).toHaveBeenCalledWith(
      "/api/templates?artifact_id=v1%2FL5_UFLOW%2Fv005",
      expect.objectContaining({ method: "GET" }),
    );
  });

  it("rejects catalogs with unknown item types at the parse boundary", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        jsonResponse({
          ok: true,
          ...catalog,
          item_types: { targets: ["UNOBTAINIUM"], inputs: ["RAW_A"] },
        }),
      ),
    );

    await expect(api.templates()).rejects.toMatchObject({
      status: 200,
      code: "INVALID_RESPONSE",
    });
  });

  it("queries fleet availability for a specific artifact", async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      jsonResponse({
        ok: true,
        fleet: {
          modes: ["single", "dual"],
          default: "single",
          policy_version: "v1_fleet_test",
          artifact_id: "v1/L5_UFLOW/v005",
          dual: { available: false, reason: "缺少 Cart2" },
        },
      }),
    );
    vi.stubGlobal("fetch", fetchMock);

    const result = await api.fleet("v1/L5_UFLOW/v005");

    expect(result.fleet.dual.available).toBe(false);
    expect(fetchMock).toHaveBeenCalledWith(
      "/api/fleet?artifact_id=v1%2FL5_UFLOW%2Fv005",
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
