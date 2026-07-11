import { useApp } from "../app/AppContext";
import { useApi } from "../hooks/useApi";
import type { PartyMemberView } from "../api/types";
import { Chip, EmptyState, ErrorState, LoadingSkeleton, Surface, SectionHeader } from "../design-system/core";
import { HealthBar } from "../design-system/stats";

export function Party() {
  const { campaignId } = useApp();
  const { data, error, loading, refresh } =
    useApi<{ members: PartyMemberView[] }>(`/campaigns/${campaignId}/grimoire/party`);

  if (loading) return <LoadingSkeleton rows={3} />;
  if (error) return <ErrorState message={error} onRetry={refresh} />;
  if (!data || data.members.length === 0) {
    return <EmptyState glyph="⁂" title="ยังไม่มีตัวละครในปาร์ตี้" />;
  }

  return (
    <>
      <SectionHeader title="ปาร์ตี้" sub={`${data.members.length} คน`} />
      <div className="two-col">
        {data.members.map((m) => (
          <Surface key={m.character_id}>
            <div style={{ display: "flex", gap: 12, alignItems: "center" }}>
              <div className="entity-item" style={{ padding: 0, border: "none", cursor: "default" }}>
                <span className="sigil" aria-hidden>{m.name.charAt(0)}</span>
              </div>
              <div style={{ minWidth: 0 }}>
                <div style={{ fontWeight: 600 }}>
                  {m.name}
                  {m.is_you && <span style={{ color: "var(--gold)", fontSize: 12 }}> · ตัวเจ้า</span>}
                </div>
                <div style={{ color: "var(--text-3)", fontSize: 13 }}>
                  {m.player_name} · {m.char_class} เลเวล {m.level} · {m.species}
                </div>
              </div>
            </div>
            {m.is_you && m.hp !== undefined && m.max_hp !== undefined && (
              <HealthBar hp={m.hp} maxHp={m.max_hp} />
            )}
            <div style={{ display: "flex", gap: 6, flexWrap: "wrap", marginTop: 8 }}>
              {m.location_name && <Chip tone="rain">{m.location_name}</Chip>}
              {m.observable.map((o) => <Chip key={o} tone="condition">{o}</Chip>)}
              {!m.is_you && m.observable.length === 0 && (
                <span style={{ color: "var(--text-3)", fontSize: 13 }}>ดูปกติดี</span>
              )}
            </div>
          </Surface>
        ))}
      </div>
      <p style={{ color: "var(--text-3)", fontSize: 13, marginTop: 14 }}>
        เจ้าเห็นเฉพาะสภาพที่สังเกตได้ของเพื่อนร่วมทาง — ตัวเลขจริงเป็นของผู้เล่นแต่ละคน
      </p>
    </>
  );
}
