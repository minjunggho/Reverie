import { useApp } from "../app/AppContext";
import { useApi } from "../hooks/useApi";
import type { CommandCenterPayload } from "../api/types";
import { Chip, ErrorState, LoadingSkeleton, Surface, SectionHeader, VisBadge } from "../design-system/core";

export function CommandCenter() {
  const { campaignId, navigate } = useApp();
  const { data, error, loading, refresh } =
    useApi<CommandCenterPayload>(`/campaigns/${campaignId}/studio/command-center`);

  if (loading) return <LoadingSkeleton rows={5} />;
  if (error) return <ErrorState message={error} onRetry={refresh} />;
  if (!data) return null;

  return (
    <>
      <div style={{ display: "flex", alignItems: "baseline", gap: 12, flexWrap: "wrap" }}>
        <h1 style={{ fontFamily: "var(--font-display)", fontSize: 24, color: "var(--gold)" }}>
          {data.campaign.name}
        </h1>
        <Chip tone={data.campaign.status === "ACTIVE" ? "success" : ""}>{data.campaign.status}</Chip>
        <span style={{ color: "var(--text-3)", fontSize: 13 }}>{data.game_time_th}</span>
      </div>
      {data.campaign.central_question && (
        <p style={{ color: "var(--text-2)", fontFamily: "var(--font-display)", marginTop: 4 }}>
          {data.campaign.central_question}
        </p>
      )}

      {data.warnings.length > 0 && (
        <div className="warn-box" role="alert">
          {data.warnings.map((w, i) => <div key={i}>⚠ {w}</div>)}
        </div>
      )}

      <div className="two-col" style={{ marginTop: 14 }}>
        <Surface>
          <SectionHeader title="ตอนนี้"
                         action={<button className="btn small" onClick={() => navigate("studio/scene")}>เปิดฉาก →</button>} />
          <dl className="kv">
            <dt>เซสชัน</dt>
            <dd>{data.session ? `ที่ ${data.session.number} · ${data.session.status} · ${data.session.play_state}` : "ไม่มีเซสชันที่กำลังเล่น"}</dd>
            <dt>ฉาก</dt>
            <dd>{data.scene ? `${data.scene.location_name ?? "?"} · ${data.scene.mode}` : "—"}</dd>
            <dt>เป้าหมายฉาก</dt>
            <dd>{data.scene?.purpose || "—"}</dd>
            <dt>Session prep</dt>
            <dd>{data.campaign.session_purpose || "—"}</dd>
          </dl>
        </Surface>

        <Surface>
          <SectionHeader title="ปาร์ตี้อยู่ที่ไหน" />
          {data.party.map((p) => (
            <div key={p.character_id} className="stat-row">
              <div className="name" style={{ flexDirection: "column", alignItems: "flex-start", gap: 0 }}>
                <span className="th">{p.name} <span style={{ color: "var(--text-3)", fontSize: 12 }}>({p.player_name})</span></span>
                <span className="en">{p.location_name ?? "ยังไม่ระบุตำแหน่ง"}</span>
              </div>
              <div style={{ textAlign: "right" }}>
                <div className="num">{p.hp}/{p.max_hp}</div>
                {p.conditions.length > 0 && (
                  <div style={{ fontSize: 11.5, color: "#d98a8e" }}>{p.conditions.join(", ")}</div>
                )}
              </div>
            </div>
          ))}
        </Surface>
      </div>

      <SectionHeader title="แรงกดดันของโลก"
                     action={<button className="btn small" onClick={() => navigate("studio/threats")}>ทั้งหมด →</button>} />
      <Surface tight>
        {data.threats.length === 0 && <p style={{ color: "var(--text-3)" }}>ไม่มีภัยคุกคามที่เคลื่อนไหว</p>}
        {data.threats.map((t) => (
          <div key={t.id} className="stat-row" style={{ flexDirection: "column", alignItems: "stretch", gap: 6 }}>
            <div style={{ display: "flex", justifyContent: "space-between", gap: 10 }}>
              <span style={{ fontWeight: 550 }}>{t.name}</span>
              <span style={{ color: "var(--text-3)", fontSize: 13 }}>{t.progress}%</span>
            </div>
            <div className="progress-thin"><div style={{ width: `${t.progress}%` }} /></div>
            <span style={{ color: "var(--text-2)", fontSize: 13.5 }}>ต่อไป: {t.next_action || t.goal}</span>
          </div>
        ))}
        {data.due_events.length > 0 && (
          <>
            <hr className="hr" />
            {data.due_events.map((e) => (
              <div key={e.id} className="stat-row">
                <div className="name">
                  <span className="th">{e.summary || e.kind}</span>
                  {e.perceivable && <Chip tone="rain">ผู้เล่นรับรู้ได้</Chip>}
                </div>
                <span style={{ color: "var(--text-3)", fontSize: 13 }}>{e.due_th}</span>
              </div>
            ))}
          </>
        )}
      </Surface>

      <SectionHeader title="เหตุการณ์ล่าสุด"
                     action={<button className="btn small" onClick={() => navigate("studio/events")}>ตัวตรวจสอบ →</button>} />
      <Surface tight>
        {data.recent_events.map((e) => (
          <div key={e.seq} className="stat-row">
            <div className="name" style={{ minWidth: 0 }}>
              <span className="th" style={{ overflow: "hidden", textOverflow: "ellipsis" }}>{e.summary || e.event_type}</span>
            </div>
            <VisBadge visibility={e.visibility} />
          </div>
        ))}
      </Surface>
    </>
  );
}
