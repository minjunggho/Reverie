import { useApp } from "../app/AppContext";
import { useApi } from "../hooks/useApi";
import type { ThreatsPayload } from "../api/types";
import { Chip, EmptyState, ErrorState, LoadingSkeleton, Surface, SectionHeader } from "../design-system/core";

export function ThreatsView() {
  const { campaignId } = useApp();
  const { data, error, loading, refresh } =
    useApi<ThreatsPayload>(`/campaigns/${campaignId}/studio/threats`);

  if (loading) return <LoadingSkeleton rows={4} />;
  if (error) return <ErrorState message={error} onRetry={refresh} />;
  if (!data || (data.threats.length === 0 && data.scheduled_events.length === 0)) {
    return <EmptyState glyph="▲" title="โลกยังสงบ — ไม่มีภัยคุกคามที่บันทึกไว้" />;
  }

  return (
    <>
      <SectionHeader title="ภัยคุกคามและฝ่ายต่างๆ" sub="เดินหน้าเองตามเวลาในโลก" />
      {data.threats.map((t) => (
        <Surface key={t.id}>
          <div style={{ display: "flex", justifyContent: "space-between", gap: 10, flexWrap: "wrap" }}>
            <h3 style={{ fontSize: 16.5 }}>{t.name}</h3>
            <Chip tone={t.status === "active" ? "condition" : t.status === "resolved" ? "success" : ""}>
              {t.status}
            </Chip>
          </div>
          <p style={{ color: "var(--text-2)", margin: "6px 0" }}>{t.goal}</p>
          <div className="progress-thin" role="meter" aria-label={`ความคืบหน้า ${t.progress}%`}
               aria-valuenow={t.progress} aria-valuemin={0} aria-valuemax={100}>
            <div style={{ width: `${t.progress}%` }} />
          </div>
          <div style={{ display: "flex", justifyContent: "space-between", marginTop: 6,
                        color: "var(--text-3)", fontSize: 13, flexWrap: "wrap", gap: 6 }}>
            <span>ต่อไป: <span style={{ color: "var(--text-2)" }}>{t.next_action || "—"}</span></span>
            <span>ขยับ +{t.tick_amount} ทุก {t.tick_interval} นาที · ครั้งถัดไป {t.due_th}</span>
          </div>
        </Surface>
      ))}

      {data.scheduled_events.length > 0 && (
        <>
          <SectionHeader title="เหตุการณ์ตามกำหนด" />
          <Surface tight>
            {data.scheduled_events.map((e) => (
              <div key={e.id} className="stat-row">
                <div className="name">
                  <span className="th">{e.summary || e.kind}</span>
                  {e.perceivable && <Chip tone="rain">ผู้เล่นรับรู้ได้</Chip>}
                  {e.resolved && <Chip tone="success">เกิดขึ้นแล้ว</Chip>}
                </div>
                <span style={{ color: "var(--text-3)", fontSize: 13 }}>{e.due_th}</span>
              </div>
            ))}
          </Surface>
        </>
      )}

      <p style={{ color: "var(--text-3)", fontSize: 13, marginTop: 14 }}>
        ความคืบหน้าเปลี่ยนได้ผ่านเหตุการณ์ในเกมเท่านั้น — มุมมองนี้อ่านอย่างเดียว
      </p>
    </>
  );
}
