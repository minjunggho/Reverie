import { useMemo, useState } from "react";
import { useApp } from "../app/AppContext";
import { useApi } from "../hooks/useApi";
import type { NpcDetail, NpcListItem } from "../api/types";
import { Chip, EmptyState, ErrorState, LoadingSkeleton, Sheet, Surface, SectionHeader, VisBadge } from "../design-system/core";

const STATUS_TH: Record<string, string> = {
  KNOWS: "รู้", BELIEVES: "เชื่อ", SUSPECTS: "สงสัย", HEARD_RUMOR: "ได้ยินข่าวลือ",
};

function NpcDetailSheet({ npcId, onClose }: { npcId: string; onClose: () => void }) {
  const { campaignId } = useApp();
  const { data, error, loading } =
    useApi<NpcDetail>(`/campaigns/${campaignId}/studio/npcs/${npcId}`);
  return (
    <Sheet title={data?.npc.name ?? "NPC"} onClose={onClose}>
      {loading && <LoadingSkeleton rows={4} />}
      {error && <ErrorState message={error} />}
      {data && (
        <>
          {/* OBJECTIVE CANON */}
          <h4 style={{ color: "var(--gold)", fontSize: 13, letterSpacing: "0.06em" }}>ความจริงของโลก</h4>
          <dl className="kv" style={{ marginBottom: 12 }}>
            <dt>อยู่ที่</dt><dd>{data.npc.location_name ?? "ไม่ระบุ"}</dd>
            <dt>การสื่อสาร</dt><dd>{data.npc.communication_mode}</dd>
            <dt>บุคลิก</dt><dd>{data.npc.personality || "—"}</dd>
            <dt>น้ำเสียง</dt><dd>{data.npc.voice_register || "—"}</dd>
            <dt>เป้าหมาย</dt><dd>{data.npc.goals.join("; ") || "—"}</dd>
            <dt>อารมณ์</dt><dd>{data.npc.emotional_state}</dd>
          </dl>

          {data.protocols.length > 0 && (
            <>
              <h4 style={{ color: "var(--gold)", fontSize: 13, letterSpacing: "0.06em" }}>กฎที่ NPC นี้รู้</h4>
              {data.protocols.map((p, i) => (
                <div key={i} style={{ marginBottom: 10 }}>
                  <div style={{ fontWeight: 550 }}>{p.title}</div>
                  <ol style={{ margin: "4px 0 0", paddingLeft: 22, color: "var(--text-2)" }}>
                    {p.rules.map((r, j) => <li key={j}>{r}</li>)}
                  </ol>
                </div>
              ))}
            </>
          )}

          {/* EPISTEMIC STATE — kept visually separate from canon. */}
          <hr className="hr" />
          <h4 style={{ color: "var(--rain)", fontSize: 13, letterSpacing: "0.06em" }}>
            สิ่งที่ NPC นี้รู้ / เชื่อ (ไม่ใช่ความจริงเสมอไป)
          </h4>
          {data.knowledge.length === 0 && <p style={{ color: "var(--text-3)" }}>ยังไม่มีบันทึกความรู้</p>}
          {data.knowledge.map((k, i) => (
            <div key={i} className="stat-row" style={{ alignItems: "flex-start" }}>
              <div className="name" style={{ flexDirection: "column", alignItems: "flex-start", gap: 2 }}>
                <span className="th">{k.fact}</span>
                <span className="en">{k.subject} · ความมั่นใจ {Math.round(k.confidence * 100)}%</span>
              </div>
              <Chip tone={k.status === "KNOWS" ? "success" : "rain"}>{STATUS_TH[k.status] ?? k.status}</Chip>
            </div>
          ))}

          {data.relationships.length > 0 && (
            <>
              <h4 style={{ color: "var(--rain)", fontSize: 13, letterSpacing: "0.06em", marginTop: 12 }}>ความสัมพันธ์</h4>
              {data.relationships.map((r, i) => (
                <div key={i} className="stat-row">
                  <span className="th">{r.entity_name}</span>
                  <span style={{ color: "var(--text-2)", fontSize: 13 }}>
                    {r.attitude} · trust {r.trust}
                  </span>
                </div>
              ))}
            </>
          )}

          {data.recent_events.length > 0 && (
            <>
              <hr className="hr" />
              <h4 style={{ color: "var(--text-2)", fontSize: 13 }}>เหตุการณ์ล่าสุด</h4>
              {data.recent_events.map((e) => (
                <div key={e.seq} className="stat-row">
                  <span className="th" style={{ fontSize: 14 }}>{e.summary || e.event_type}</span>
                  <VisBadge visibility={e.visibility} />
                </div>
              ))}
            </>
          )}
        </>
      )}
    </Sheet>
  );
}

export function NpcsView() {
  const { campaignId } = useApp();
  const { data, error, loading, refresh } =
    useApi<{ npcs: NpcListItem[] }>(`/campaigns/${campaignId}/studio/npcs`);
  const [q, setQ] = useState("");
  const [presentOnly, setPresentOnly] = useState(false);
  const [openId, setOpenId] = useState<string | null>(null);

  const npcs = useMemo(() => {
    if (!data) return [];
    return data.npcs.filter((n) =>
      (!presentOnly || n.present_in_scene) &&
      (!q || n.name.toLowerCase().includes(q.toLowerCase())));
  }, [data, q, presentOnly]);

  if (loading) return <LoadingSkeleton rows={5} />;
  if (error) return <ErrorState message={error} onRetry={refresh} />;
  if (!data || data.npcs.length === 0) {
    return <EmptyState glyph="♟" title="ยังไม่มี NPC ในแคมเปญ" />;
  }

  return (
    <>
      <SectionHeader title="NPC" sub={`${data.npcs.length} ตัว`} />
      <div className="filterbar">
        <input className="searchfield" style={{ maxWidth: 260 }} value={q}
               onChange={(e) => setQ(e.target.value)} placeholder="ค้นหา NPC…"
               aria-label="ค้นหา NPC" />
        <button className={`btn small ${presentOnly ? "primary" : ""}`}
                aria-pressed={presentOnly}
                onClick={() => setPresentOnly(!presentOnly)}>
          เฉพาะที่อยู่ในฉาก
        </button>
      </div>
      <Surface tight>
        {npcs.map((n) => (
          <button key={n.id} className="entity-item" onClick={() => setOpenId(n.id)}>
            <span className="sigil" aria-hidden>{n.name.charAt(0)}</span>
            <span className="body">
              <span className="title">
                {n.name}
                {n.present_in_scene && <span style={{ color: "var(--success)", fontSize: 12 }}> · อยู่ในฉาก</span>}
              </span>
              <span className="meta">
                {n.location_name ?? "ไม่ระบุตำแหน่ง"} · {n.emotional_state}
                {n.communication_mode !== "SPOKEN" && ` · ${n.communication_mode}`}
              </span>
            </span>
            {n.goals.length > 0 && (
              <span style={{ color: "var(--text-3)", fontSize: 12.5, maxWidth: 220,
                             overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                {n.goals[0]}
              </span>
            )}
          </button>
        ))}
        {npcs.length === 0 && <EmptyState title="ไม่พบ NPC ตามตัวกรอง" />}
      </Surface>

      {openId && <NpcDetailSheet npcId={openId} onClose={() => setOpenId(null)} />}
    </>
  );
}
