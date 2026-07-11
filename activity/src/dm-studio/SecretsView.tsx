import { useApp } from "../app/AppContext";
import { useApi } from "../hooks/useApi";
import type { SecretsPayload } from "../api/types";
import { Chip, EmptyState, ErrorState, LoadingSkeleton, Surface, SectionHeader, VisBadge } from "../design-system/core";

export function SecretsView() {
  const { campaignId } = useApp();
  const { data, error, loading, refresh } =
    useApi<SecretsPayload>(`/campaigns/${campaignId}/studio/secrets`);

  if (loading) return <LoadingSkeleton rows={4} />;
  if (error) return <ErrorState message={error} onRetry={refresh} />;
  if (!data) return null;

  return (
    <>
      <SectionHeader title="ความลับ" sub="DM เท่านั้น — ไม่มีทางไปถึงผู้เล่น" />
      {data.secrets.length === 0 && <EmptyState glyph="🗝" title="ยังไม่มีความลับที่บันทึกไว้" />}
      {data.secrets.map((s) => (
        <Surface key={s.id}>
          <div style={{ display: "flex", gap: 8, alignItems: "flex-start", flexWrap: "wrap" }}>
            <span aria-hidden style={{ color: "var(--danger)" }}>🗝</span>
            <div style={{ flex: 1, minWidth: 200 }}>
              <p style={{ fontWeight: 550, margin: 0 }}>{s.fact}</p>
              <div style={{ display: "flex", gap: 6, marginTop: 6, flexWrap: "wrap" }}>
                <VisBadge visibility={s.visibility} />
                {s.revealed
                  ? <Chip tone="gold">ถูกเผยแล้ว {s.known_by.length > 0 && `(${s.known_by.length} คน)`}</Chip>
                  : <Chip>ยังไม่ถูกเผย</Chip>}
              </div>
            </div>
          </div>
          {s.clues.length > 0 && (
            <>
              <hr className="hr" />
              <div style={{ fontSize: 13, color: "var(--text-3)", marginBottom: 6 }}>
                เส้นทางเบาะแส ({s.clues.filter((c) => c.known).length}/{s.clues.length} ถูกค้นพบ)
              </div>
              {s.clues.map((c) => (
                <div key={c.id} className="stat-row">
                  <span className="th" style={{ fontSize: 14.5, color: c.known ? "var(--text)" : "var(--text-2)" }}>
                    {c.known ? "●" : "○"} {c.text}
                  </span>
                  {c.known
                    ? <Chip tone="success">ปาร์ตี้รู้แล้ว</Chip>
                    : <Chip>ยังไม่ถูกค้นพบ</Chip>}
                </div>
              ))}
            </>
          )}
        </Surface>
      ))}

      {data.protocols.length > 0 && (
        <>
          <SectionHeader title="กฎ/พิธีการที่ประกาศไว้ (Protocols)" sub="ลำดับคือความจริง — ห้ามสลับ" />
          {data.protocols.map((p) => (
            <Surface key={p.id}>
              <div style={{ display: "flex", justifyContent: "space-between", gap: 8, flexWrap: "wrap" }}>
                <h3 style={{ fontSize: 15.5 }}>{p.title}</h3>
                <VisBadge visibility={p.visibility} />
              </div>
              <ol style={{ margin: "8px 0 0", paddingLeft: 24, color: "var(--text-2)" }}>
                {p.rules.map((r, i) => <li key={i} style={{ padding: "2px 0" }}>{r}</li>)}
              </ol>
              {p.known_by.length > 0 && (
                <div style={{ display: "flex", gap: 6, marginTop: 10, flexWrap: "wrap" }}>
                  <span style={{ color: "var(--text-3)", fontSize: 13 }}>รู้โดย:</span>
                  {p.known_by.map((n) => <Chip key={n}>{n}</Chip>)}
                </div>
              )}
            </Surface>
          ))}
        </>
      )}
    </>
  );
}
