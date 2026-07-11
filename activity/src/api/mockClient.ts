/** Mock API client — dev-only visual development without Discord or a backend.
 * Guarded at the call site: mock mode is unreachable in production builds. */
import type { ReverieApi } from "./client";
import { ApiError } from "./client";
import * as fx from "../mocks/fixtures";

const LATENCY_MS = 250;

function route(path: string): unknown {
  if (path.startsWith("/context")) return fx.mockContext;
  const gm = path.match(/\/campaigns\/[^/]+\/grimoire\/(\w+)/);
  if (gm) {
    switch (gm[1]) {
      case "overview": return fx.mockOverview;
      case "skills": return fx.mockSkills;
      case "spellbook": return fx.mockSpellbook;
      case "features": return fx.mockFeatures;
      case "inventory": return fx.mockInventory;
      case "story": return fx.mockStory;
      case "party": return fx.mockParty;
      case "chronicle": return fx.mockChronicle;
    }
  }
  const sm = path.match(/\/campaigns\/[^/]+\/studio\/([\w-]+)(?:\/([\w-]+))?(?:\/(\w+))?/);
  if (sm) {
    switch (sm[1]) {
      case "command-center": return fx.mockCommandCenter;
      case "scene": return fx.mockScene;
      case "world": return fx.mockWorld;
      case "npcs": return sm[2] ? fx.mockNpcDetail : fx.mockNpcs;
      case "threats": return fx.mockThreats;
      case "secrets": return fx.mockSecrets;
      case "events": return fx.mockEvents;
      case "imports": return fx.mockImports;
    }
  }
  throw new ApiError(404, `no mock for ${path}`);
}

export class MockApi implements ReverieApi {
  async get<T>(path: string): Promise<T> {
    await new Promise((r) => setTimeout(r, LATENCY_MS));
    return route(path) as T;
  }

  async post<T>(path: string): Promise<T> {
    await new Promise((r) => setTimeout(r, LATENCY_MS));
    if (path.includes("/imports/") && path.endsWith("/approve")) {
      fx.mockImports.imports = fx.mockImports.imports.map((i) =>
        i.status === "PENDING_REVIEW" ? { ...i, status: "APPROVED" } : i);
      return { status: "APPROVED", counts: { locations: 7 }, warnings: [] } as T;
    }
    if (path.includes("/imports/") && path.endsWith("/reject")) {
      fx.mockImports.imports = fx.mockImports.imports.map((i) =>
        i.status === "PENDING_REVIEW" ? { ...i, status: "REJECTED" } : i);
      return { status: "REJECTED" } as T;
    }
    if (path.endsWith("/repair")) return { status: "REPAIRED", protocols_added: 1 } as T;
    throw new ApiError(404, `no mock for POST ${path}`);
  }
}
