import { ReactNode, createContext, useContext } from "react";
import type { ReverieApi } from "../api/client";
import type { ActivityContext } from "../api/types";

export interface AppState {
  api: ReverieApi;
  context: ActivityContext;
  campaignId: string;
  refreshContext: () => Promise<void>;
  view: string;                      // e.g. "grimoire/overview" | "studio/command"
  navigate: (view: string) => void;
}

const Ctx = createContext<AppState | null>(null);

export function AppProvider({ value, children }: { value: AppState; children: ReactNode }) {
  return <Ctx.Provider value={value}>{children}</Ctx.Provider>;
}

export function useApp(): AppState {
  const v = useContext(Ctx);
  if (!v) throw new Error("useApp outside AppProvider");
  return v;
}
