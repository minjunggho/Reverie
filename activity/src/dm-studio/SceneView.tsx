import { useApp } from "../app/AppContext";
import { useApi } from "../hooks/useApi";
import type { ScenePayload } from "../api/types";
import { Chip, EmptyState, ErrorState, LoadingSkeleton, Surface, SectionHeader, VisBadge } from "../design-system/core";

export function SceneView() {
  const { campaignId } = useApp();
  const { data, error, loading, refresh } =
    useApi<ScenePayload>(`/campaigns/${campaignId}/studio/scene`);

  if (loading) return <LoadingSkeleton rows={5} />;
  if (error) return <ErrorState message={error} onRetry={refresh} />;
  if (!data || !data.scene) {
    return <EmptyState glyph="◉" title="ไม่มีฉากที่กำลังเล่น"
                       hint="เริ่มเซสชันใน Discord ด้วย !rv session start" />;
  }

  return (
    <>
      {/* CANONICAL LOCATION — the world truth; a Scene merely happens here. */}
      <SectionHeader title="สถานที่ (canon)" sub={data.location?.provenance} />
      <Surface>
        <h3 style={{ fontFamily: "var(--font-display)", color: "var(--gold)", fontSize: 19 }}>
          {data.location?.name}
        </h3>
        {data.location?.parent_path && (
          <p style={{ color: "var(--text-3)", fontSize: 13 }}>{data.location.parent_path}</p>
        )}
        <p style={{ color: "var(--text-2)" }}>{data.location?.obvious}</p>
        {data.location?.current_activity && (
          <p style={{ color: "var(--rain)", fontSize: 14 }}>กำลังเกิดขึ้น: {data.location.current_activity}</p>
        )}
        {data.exits.length > 0 && (
          <>
            <hr className="hr" />
            <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
              {data.exits.map((e, i) => (
                <Chip key={i} tone={e.access_state === "open" ? "" : "condition"}>
                  {e.label} → {e.to_name}
                  {e.travel_minutes > 0 && ` (${e.travel_minutes} นาที)`}
                  {e.access_state !== "open" && ` [${e.access_state}]`}
                </Chip>
              ))}
            </div>
          </>
        )}
      </Surface>

      {/* CURRENT SCENE STATE — working state, distilled to Events on close. */}
      <SectionHeader title="สถานะฉากปัจจุบัน" sub={`${data.scene.mode} · ${data.scene.status}`} />
      <Surface>
        <dl className="kv">
          <dt>เป้าหมายฉาก</dt><dd>{data.scene.purpose || "—"}</dd>
          <dt>คำถามดราม่า</dt><dd>{data.scene.dramatic_question || "—"}</dd>
          <dt>เริ่มเมื่อ</dt><dd>{data.scene.start_game_time_th}</dd>
          <dt>ค้างอยู่</dt><dd>{data.scene.pending_action ?? "ไม่มี"}</dd>
        </dl>
        {data.scene.allowed_clues.length > 0 && (
          <>
            <hr className="hr" />
            <div style={{ fontSize: 13, color: "var(--text-3)", marginBottom: 6 }}>
              เบาะแสที่ฉากนี้อาจเผยได้ (authored เท่านั้น)
            </div>
            {data.scene.allowed_clues.map((c, i) => (
              <div key={i} style={{ color: "var(--text-2)", fontSize: 14 }}>• {c}</div>
            ))}
          </>
        )}
      </Surface>

      {/* PRESENT ENTITIES — hard invariant: position must match the scene. */}
      <SectionHeader title="ผู้ที่อยู่ในฉาก" />
      <div className="two-col">
        <Surface tight>
          <div style={{ fontSize: 12.5, color: "var(--text-3)", padding: "4px 4px 8px" }}>ตัวละครผู้เล่น</div>
          {data.participants.map((p) => (
            <div key={p.ref} className="stat-row">
              <span className="th">{p.name}</span>
              <span className="num">{p.hp}/{p.max_hp}</span>
            </div>
          ))}
        </Surface>
        <Surface tight>
          <div style={{ fontSize: 12.5, color: "var(--text-3)", padding: "4px 4px 8px" }}>NPC ที่อยู่จริง</div>
          {data.present_npcs.length === 0 && <p style={{ color: "var(--text-3)", padding: 4 }}>ไม่มี</p>}
          {data.present_npcs.map((n) => (
            <div key={n.ref} className="stat-row">
              <div className="name">
                <span className="th">{n.name}</span>
                {n.communication_mode !== "SPOKEN" && <Chip tone="rain">{n.communication_mode}</Chip>}
              </div>
              <span style={{ color: "var(--text-3)", fontSize: 13 }}>{n.emotional_state}</span>
            </div>
          ))}
        </Surface>
      </div>

      {data.stale_refs.length > 0 && (
        <div className="warn-box" role="alert">
          {data.stale_refs.map((s, i) => (
            <div key={i}>⚠ อ้างอิงค้างเก่า: {s.reason} — ไม่ถูกนับว่าอยู่ในฉาก</div>
          ))}
        </div>
      )}

      <SectionHeader title="เหตุการณ์ในฉากนี้" />
      <Surface tight>
        {data.recent_events.length === 0 && <p style={{ color: "var(--text-3)" }}>ยังไม่มี</p>}
        {data.recent_events.map((e) => (
          <div key={e.seq} className="stat-row">
            <span className="th" style={{ minWidth: 0, overflow: "hidden", textOverflow: "ellipsis" }}>
              {e.summary || e.event_type}
            </span>
            <VisBadge visibility={e.visibility} />
          </div>
        ))}
      </Surface>
    </>
  );
}
