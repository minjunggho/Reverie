import { useMemo, useState } from "react";
import { useApp } from "../app/AppContext";
import { useApi } from "../hooks/useApi";
import type { WorldLocation } from "../api/types";
import { Chip, EmptyState, ErrorState, LoadingSkeleton, Sheet, Surface, SectionHeader } from "../design-system/core";

const PROVENANCE_TH: Record<string, string> = {
  AUTHORED: "เขียนเอง", IMPORTED: "นำเข้า", AI_EXPANDED: "AI ขยาย",
};

export function WorldView() {
  const { campaignId } = useApp();
  const { data, error, loading, refresh } =
    useApi<{ locations: WorldLocation[] }>(`/campaigns/${campaignId}/studio/world`);
  const [q, setQ] = useState("");
  const [prov, setProv] = useState("all");
  const [open, setOpen] = useState<WorldLocation | null>(null);

  const tree = useMemo(() => {
    if (!data) return [];
    const match = (l: WorldLocation) =>
      (prov === "all" || l.provenance === prov) &&
      (!q || l.name.toLowerCase().includes(q.toLowerCase()));
    const byParent = new Map<string | null, WorldLocation[]>();
    for (const l of data.locations) {
      byParent.set(l.parent_id, [...(byParent.get(l.parent_id) ?? []), l]);
    }
    const out: { loc: WorldLocation; depth: number }[] = [];
    const walk = (parent: string | null, depth: number) => {
      for (const l of byParent.get(parent) ?? []) {
        out.push({ loc: l, depth });
        walk(l.id, depth + 1);
      }
    };
    walk(null, 0);
    // Orphans whose parent isn't in the list (defensive).
    const seen = new Set(out.map((o) => o.loc.id));
    for (const l of data.locations) if (!seen.has(l.id)) out.push({ loc: l, depth: 0 });
    return out.filter((o) => match(o.loc));
  }, [data, q, prov]);

  if (loading) return <LoadingSkeleton rows={6} />;
  if (error) return <ErrorState message={error} onRetry={refresh} />;
  if (!data || data.locations.length === 0) {
    return <EmptyState glyph="✦" title="ยังไม่มีภูมิศาสตร์ของโลก"
                       hint="นำเข้าแคมเปญหรือให้เอนจินสร้างสถานที่ระหว่างเล่น" />;
  }

  return (
    <>
      <SectionHeader title="โลก" sub={`${data.locations.length} สถานที่`} />
      <div className="filterbar">
        <input className="searchfield" style={{ maxWidth: 260 }} value={q}
               onChange={(e) => setQ(e.target.value)} placeholder="ค้นหาสถานที่…"
               aria-label="ค้นหาสถานที่" />
        <div className="segmented" role="tablist" aria-label="ที่มาของสถานที่">
          {["all", "IMPORTED", "AUTHORED", "AI_EXPANDED"].map((p) => (
            <button key={p} role="tab" aria-selected={prov === p}
                    className={prov === p ? "active" : ""} onClick={() => setProv(p)}>
              {p === "all" ? "ทั้งหมด" : PROVENANCE_TH[p]}
            </button>
          ))}
        </div>
      </div>
      <Surface tight>
        {tree.map(({ loc, depth }) => (
          <button key={loc.id} className="entity-item" onClick={() => setOpen(loc)}
                  style={{ paddingLeft: 6 + depth * 22 }}>
            <span className="sigil" aria-hidden>
              {loc.type === "REGION" ? "✦" : loc.type === "SETTLEMENT" ? "⌂"
                : loc.type === "DISTRICT" ? "▦" : "◆"}
            </span>
            <span className="body">
              <span className="title">{loc.name}
                {loc.party_here.length > 0 && (
                  <span style={{ color: "var(--gold)", fontSize: 12 }}> · ปาร์ตี้อยู่ที่นี่</span>
                )}
              </span>
              <span className="meta">
                {loc.type} · {PROVENANCE_TH[loc.provenance] ?? loc.provenance}
                {loc.npc_count > 0 && ` · NPC ${loc.npc_count}`}
              </span>
            </span>
          </button>
        ))}
        {tree.length === 0 && <EmptyState title="ไม่พบสถานที่ตามตัวกรอง" />}
      </Surface>

      {open && (
        <Sheet title={open.name} onClose={() => setOpen(null)}>
          <div style={{ display: "flex", gap: 8, flexWrap: "wrap", marginBottom: 10 }}>
            <Chip>{open.type}</Chip>
            <Chip tone={open.provenance === "AI_EXPANDED" ? "rain" : "gold"}>
              {PROVENANCE_TH[open.provenance] ?? open.provenance}
            </Chip>
            {open.party_here.map((n) => <Chip key={n} tone="gold">{n}</Chip>)}
          </div>
          {open.obvious && (<><h4 style={{ color: "var(--text-2)", fontSize: 13 }}>เห็นได้ทันที</h4>
            <p>{open.obvious}</p></>)}
          {open.focused && (<><h4 style={{ color: "var(--text-2)", fontSize: 13 }}>เมื่อเพ่งดู</h4>
            <p style={{ color: "var(--text-2)" }}>{open.focused}</p></>)}
          {open.hidden && (<><h4 style={{ color: "#d98a8e", fontSize: 13 }}>ซ่อนอยู่ (DM เท่านั้น)</h4>
            <p style={{ color: "var(--text-2)" }}>{open.hidden}</p></>)}
          {open.current_activity && (
            <p style={{ color: "var(--rain)" }}>กำลังเกิดขึ้น: {open.current_activity}</p>
          )}
          {open.exits.length > 0 && (
            <>
              <hr className="hr" />
              <h4 style={{ color: "var(--text-2)", fontSize: 13, marginBottom: 6 }}>ทางเชื่อม (canon)</h4>
              {open.exits.map((e, i) => (
                <div key={i} style={{ color: "var(--text-2)", fontSize: 14, padding: "3px 0" }}>
                  {e.label} → {e.to_name}
                  {e.travel_minutes > 0 && ` · ${e.travel_minutes} นาที`}
                  {e.access_state !== "open" && ` · ${e.access_state}`}
                  {!e.obvious && " · ซ่อนอยู่"}
                </div>
              ))}
            </>
          )}
        </Sheet>
      )}
    </>
  );
}
