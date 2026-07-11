import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { HttpApi, type ReverieApi } from "../api/client";
import { MockApi } from "../api/mockClient";
import type { ActivityContext } from "../api/types";
import { authenticateWithDiscord, detectLaunchMode } from "../auth/discord";
import { AppProvider } from "./AppContext";
import { AppShell } from "../components/AppShell";
import { PermissionDenied, ToastProvider } from "../design-system/core";
import { Overview } from "../grimoire/Overview";
import { Skills } from "../grimoire/Skills";
import { Spellbook } from "../grimoire/Spellbook";
import { Features } from "../grimoire/Features";
import { Inventory } from "../grimoire/Inventory";
import { Story } from "../grimoire/Story";
import { Party } from "../grimoire/Party";
import { Chronicle } from "../grimoire/Chronicle";
import { CommandCenter } from "../dm-studio/CommandCenter";
import { SceneView } from "../dm-studio/SceneView";
import { WorldView } from "../dm-studio/WorldView";
import { NpcsView } from "../dm-studio/NpcsView";
import { ThreatsView } from "../dm-studio/ThreatsView";
import { SecretsView } from "../dm-studio/SecretsView";
import { EventsView } from "../dm-studio/EventsView";
import { ImportsView } from "../dm-studio/ImportsView";

type Phase =
  | { name: "booting" }
  | { name: "outside" }
  | { name: "auth-error"; message: string }
  | { name: "ready"; api: ReverieApi; context: ActivityContext }
  | { name: "no-campaign"; context: ActivityContext }
  | { name: "expired" };

function viewFromHash(): string {
  const h = window.location.hash.replace(/^#\/?/, "");
  return h || "grimoire/overview";
}

function FullscreenNotice({ glyph, title, hint }: { glyph: string; title: string; hint?: string }) {
  return (
    <div style={{ minHeight: "100dvh", display: "flex", alignItems: "center",
                  justifyContent: "center", padding: 24 }}>
      <div className="state-block">
        <span className="glyph" aria-hidden style={{ fontSize: 42 }}>{glyph}</span>
        <h1 style={{ fontFamily: "var(--font-display)", fontSize: 20, color: "var(--gold)" }}>
          {title}
        </h1>
        {hint && <p className="hint" style={{ maxWidth: 380 }}>{hint}</p>}
      </div>
    </div>
  );
}

export function App() {
  const [phase, setPhase] = useState<Phase>({ name: "booting" });
  const [view, setView] = useState(viewFromHash());
  const httpApi = useRef(new HttpApi());

  const navigate = useCallback((v: string) => {
    window.location.hash = `/${v}`;
    setView(v);
    window.scrollTo(0, 0);
  }, []);

  useEffect(() => {
    const onHash = () => setView(viewFromHash());
    window.addEventListener("hashchange", onHash);
    return () => window.removeEventListener("hashchange", onHash);
  }, []);

  const boot = useCallback(async () => {
    const mode = detectLaunchMode();
    if (mode === "outside") {
      setPhase({ name: "outside" });
      return;
    }
    try {
      let api: ReverieApi;
      let channelId: string | null = null;
      let guildId: string | null = null;
      if (mode === "mock") {
        api = new MockApi();
      } else {
        const auth = await authenticateWithDiscord();
        httpApi.current.setToken(auth.sessionToken);
        httpApi.current.onUnauthorized = () => setPhase({ name: "expired" });
        channelId = auth.channelId;
        guildId = auth.guildId;
        api = httpApi.current;
      }
      const qs = new URLSearchParams();
      if (channelId) qs.set("channel_id", channelId);
      if (guildId) qs.set("guild_id", guildId);
      const context = await api.get<ActivityContext>(`/context?${qs.toString()}`);
      if (!context.campaign || !context.membership) {
        setPhase({ name: "no-campaign", context });
      } else {
        setPhase({ name: "ready", api, context });
      }
    } catch (e) {
      setPhase({ name: "auth-error", message: e instanceof Error ? e.message : "unknown" });
    }
  }, []);

  useEffect(() => { void boot(); }, [boot]);

  const refreshContext = useCallback(async () => {
    if (phase.name !== "ready") return;
    const context = await phase.api.get<ActivityContext>("/context");
    setPhase({ name: "ready", api: phase.api, context });
  }, [phase]);

  const appState = useMemo(() => phase.name === "ready" ? {
    api: phase.api,
    context: phase.context,
    campaignId: phase.context.campaign!.id,
    refreshContext,
    view,
    navigate,
  } : null, [phase, refreshContext, view, navigate]);

  if (phase.name === "booting") {
    return <FullscreenNotice glyph="◈" title="Reverie" hint="กำลังเชื่อมต่อกับโต๊ะของเจ้า…" />;
  }
  if (phase.name === "outside") {
    return <FullscreenNotice glyph="◈" title="Reverie Grimoire"
      hint="แอปนี้เปิดใช้ผ่าน Discord Activity ในห้องของแคมเปญ — เปิด Discord แล้วกดปุ่ม Activity ในช่องเสียงหรือช่องแชทของโต๊ะ" />;
  }
  if (phase.name === "expired") {
    return <FullscreenNotice glyph="✕" title="เซสชันหมดอายุ"
      hint="ปิดแล้วเปิด Activity อีกครั้งเพื่อยืนยันตัวตนใหม่" />;
  }
  if (phase.name === "auth-error") {
    return <FullscreenNotice glyph="✕" title="เชื่อมต่อไม่สำเร็จ" hint={phase.message} />;
  }
  if (phase.name === "no-campaign") {
    return <FullscreenNotice glyph="✦" title="ยังไม่พบโต๊ะในห้องนี้"
      hint={phase.context.my_campaigns.length > 0
        ? `เจ้าเป็นสมาชิกของ: ${phase.context.my_campaigns.map((c) => c.name).join(", ")} — เปิด Activity จากห้องของแคมเปญนั้น`
        : "สร้างโต๊ะใน Discord ด้วย !rv campaign new แล้วเปิด Activity อีกครั้ง"} />;
  }

  const isStudio = view.startsWith("studio/");
  const canStudio = phase.context.membership?.can_open_dm_studio ?? false;

  let screen: JSX.Element;
  if (isStudio && !canStudio) {
    screen = <PermissionDenied />;
  } else {
    switch (view) {
      case "grimoire/skills": screen = <Skills />; break;
      case "grimoire/spellbook": screen = <Spellbook />; break;
      case "grimoire/features": screen = <Features />; break;
      case "grimoire/inventory": screen = <Inventory />; break;
      case "grimoire/story": screen = <Story />; break;
      case "grimoire/party": screen = <Party />; break;
      case "grimoire/chronicle": screen = <Chronicle />; break;
      case "studio/command": screen = <CommandCenter />; break;
      case "studio/scene": screen = <SceneView />; break;
      case "studio/world": screen = <WorldView />; break;
      case "studio/npcs": screen = <NpcsView />; break;
      case "studio/threats": screen = <ThreatsView />; break;
      case "studio/secrets": screen = <SecretsView />; break;
      case "studio/events": screen = <EventsView />; break;
      case "studio/imports": screen = <ImportsView />; break;
      default: screen = <Overview />;
    }
  }

  return (
    <ToastProvider>
      <AppProvider value={appState!}>
        <AppShell>{screen}</AppShell>
      </AppProvider>
    </ToastProvider>
  );
}
