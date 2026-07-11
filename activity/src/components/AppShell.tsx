import { ReactNode } from "react";
import { useApp } from "../app/AppContext";

const GRIMOIRE_NAV = [
  { view: "grimoire/overview", glyph: "◈", label: "ภาพรวม" },
  { view: "grimoire/skills", glyph: "✧", label: "ทักษะ" },
  { view: "grimoire/spellbook", glyph: "❋", label: "คาถา" },
  { view: "grimoire/features", glyph: "♦", label: "ความสามารถ" },
  { view: "grimoire/inventory", glyph: "◇", label: "สัมภาระ" },
  { view: "grimoire/story", glyph: "✎", label: "เรื่องราว" },
  { view: "grimoire/party", glyph: "⁂", label: "ปาร์ตี้" },
  { view: "grimoire/chronicle", glyph: "☰", label: "บันทึก" },
];

const STUDIO_NAV = [
  { view: "studio/command", glyph: "⌘", label: "ศูนย์บัญชาการ" },
  { view: "studio/scene", glyph: "◉", label: "ฉากปัจจุบัน" },
  { view: "studio/world", glyph: "✦", label: "โลก" },
  { view: "studio/npcs", glyph: "♟", label: "NPC" },
  { view: "studio/threats", glyph: "▲", label: "ภัยคุกคาม" },
  { view: "studio/secrets", glyph: "🗝", label: "ความลับ" },
  { view: "studio/events", glyph: "≡", label: "เหตุการณ์" },
  { view: "studio/imports", glyph: "⇪", label: "นำเข้า" },
];

// Mobile shows the five most important tabs per surface; the rest are reachable
// from within pages (party/chronicle link from overview, etc.).
const MOBILE_GRIMOIRE = ["grimoire/overview", "grimoire/skills", "grimoire/spellbook",
                         "grimoire/story", "grimoire/chronicle"];
const MOBILE_STUDIO = ["studio/command", "studio/scene", "studio/npcs",
                       "studio/secrets", "studio/imports"];

export function AppShell({ children }: { children: ReactNode }) {
  const { context, view, navigate } = useApp();
  const isStudio = view.startsWith("studio/");
  const canStudio = context.membership?.can_open_dm_studio ?? false;
  const nav = isStudio ? STUDIO_NAV : GRIMOIRE_NAV;
  const mobileViews = isStudio ? MOBILE_STUDIO : MOBILE_GRIMOIRE;
  const mobileNav = nav.filter((n) => mobileViews.includes(n.view));

  return (
    <div className="shell">
      <nav className="sidebar" aria-label="เมนูหลัก">
        <div className="sidebar-brand">Reverie</div>
        <div className="sidebar-section">{isStudio ? "DM Studio" : "Grimoire"}</div>
        {nav.map((n) => (
          <button key={n.view} className={`navlink ${view === n.view ? "active" : ""}`}
                  onClick={() => navigate(n.view)}
                  aria-current={view === n.view ? "page" : undefined}>
            <span className="glyph" aria-hidden>{n.glyph}</span>
            {n.label}
          </button>
        ))}
        {canStudio && (
          <>
            <div className="sidebar-section">สลับมุมมอง</div>
            <button className="navlink" data-testid="surface-switch"
                    onClick={() => navigate(isStudio ? "grimoire/overview" : "studio/command")}>
              <span className="glyph" aria-hidden>{isStudio ? "◈" : "⌘"}</span>
              {isStudio ? "เปิด Grimoire" : "เปิด DM Studio"}
            </button>
          </>
        )}
      </nav>

      <div className="shell-main">
        <header className="topbar">
          <span className="topbar-mark">Reverie</span>
          <span className="topbar-campaign">
            {context.campaign?.name ?? "—"}
            {" · "}
            {isStudio ? "DM Studio" : context.character?.name ?? "Grimoire"}
          </span>
          <span className="topbar-status">
            <span className={`dot ${context.session?.active ? "live" : ""}`} aria-hidden />
            {context.session?.active ? `เซสชันที่ ${context.session.number}` : "ไม่มีเซสชัน"}
          </span>
          {canStudio && (
            <button className="btn small" data-testid="topbar-switch"
                    onClick={() => navigate(isStudio ? "grimoire/overview" : "studio/command")}>
              {isStudio ? "Grimoire" : "DM Studio"}
            </button>
          )}
        </header>

        <main className="shell-content">{children}</main>

        <nav className="bottomnav" aria-label="เมนูล่าง">
          {mobileNav.map((n) => (
            <button key={n.view} className={view === n.view ? "active" : ""}
                    onClick={() => navigate(n.view)}
                    aria-current={view === n.view ? "page" : undefined}>
              <span className="glyph" aria-hidden>{n.glyph}</span>
              {n.label}
            </button>
          ))}
        </nav>
      </div>
    </div>
  );
}
