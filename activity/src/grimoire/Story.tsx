import { useApp } from "../app/AppContext";
import { useApi } from "../hooks/useApi";
import type { StoryPayload } from "../api/types";
import { EmptyState, ErrorState, LoadingSkeleton, Surface, SectionHeader } from "../design-system/core";

const HOOKS: { key: keyof StoryPayload; label: string }[] = [
  { key: "origin", label: "ที่มา" },
  { key: "desire", label: "สิ่งที่ต้องการ" },
  { key: "fear", label: "สิ่งที่กลัว" },
  { key: "flaw", label: "จุดอ่อน" },
  { key: "connection", label: "สายสัมพันธ์" },
  { key: "appearance", label: "รูปลักษณ์" },
];

export function Story() {
  const { campaignId } = useApp();
  const { data, error, loading, refresh } =
    useApi<StoryPayload>(`/campaigns/${campaignId}/grimoire/story`);

  if (loading) return <LoadingSkeleton rows={4} />;
  if (error) return <ErrorState message={error} onRetry={refresh} />;
  if (!data) return null;

  const hooks = HOOKS.filter((h) => data[h.key]);

  return (
    <div className="story-prose">
      {data.concept
        ? <p className="lead">{data.concept}</p>
        : <EmptyState glyph="✎" title="ยังไม่มีเรื่องราวของตัวละคร"
                      hint="เรื่องราวจะปรากฏเมื่อสร้างตัวละครผ่านบทสนทนาใน Discord" />}

      {hooks.length > 0 && (
        <Surface>
          <dl className="kv">
            {hooks.map((h) => (
              <div key={h.key} style={{ display: "contents" }}>
                <dt>{h.label}</dt>
                <dd>{String(data[h.key])}</dd>
              </div>
            ))}
          </dl>
        </Surface>
      )}

      {(data.brief || data.central_question) && (
        <>
          <SectionHeader title="โลกที่รู้จัก" />
          <Surface>
            {data.brief && <p style={{ color: "var(--text-2)" }}>{data.brief}</p>}
            {data.central_question && (
              <p style={{ color: "var(--gold)", fontFamily: "var(--font-display)", marginTop: 10 }}>
                {data.central_question}
              </p>
            )}
          </Surface>
        </>
      )}

      <SectionHeader title="สิ่งที่ค้นพบ" sub="เฉพาะเจ้าเท่านั้นที่รู้" />
      {data.discoveries.length === 0
        ? <Surface tight><p style={{ color: "var(--text-3)" }}>ยังไม่มีการค้นพบส่วนตัว</p></Surface>
        : (
          <Surface tight>
            <div className="timeline">
              {data.discoveries.map((d) => (
                <div key={d.seq} className="timeline-entry private">
                  <div className="when">{d.game_time_th}</div>
                  <div className="what">{d.summary}</div>
                </div>
              ))}
            </div>
          </Surface>
        )}
    </div>
  );
}
