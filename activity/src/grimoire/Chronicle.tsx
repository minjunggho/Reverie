import { useMemo, useState } from "react";
import { useApp } from "../app/AppContext";
import { useApi } from "../hooks/useApi";
import type { ChroniclePayload } from "../api/types";
import { Chip, EmptyState, ErrorState, LoadingSkeleton, Surface, SectionHeader, Segmented } from "../design-system/core";

type Filter = "all" | "private" | "travel" | "knowledge";

export function Chronicle() {
  const { campaignId } = useApp();
  const { data, error, loading, refresh } =
    useApi<ChroniclePayload>(`/campaigns/${campaignId}/grimoire/chronicle`);
  const [filter, setFilter] = useState<Filter>("all");

  const entries = useMemo(() => {
    if (!data) return [];
    switch (filter) {
      case "private": return data.entries.filter((e) => e.private);
      case "travel": return data.entries.filter((e) => e.event_type === "CHARACTER_MOVED");
      case "knowledge": return data.entries.filter((e) => e.event_type === "KNOWLEDGE_GAINED");
      default: return data.entries;
    }
  }, [data, filter]);

  if (loading) return <LoadingSkeleton rows={5} />;
  if (error) return <ErrorState message={error} onRetry={refresh} />;
  if (!data || data.entries.length === 0) {
    return <EmptyState glyph="☰" title="บันทึกยังว่าง"
                       hint="เรื่องราวเพิ่งเริ่มต้น — เหตุการณ์จะถูกจดไว้ที่นี่" />;
  }

  // Group by session for a journal feel (not a database log).
  const groups = new Map<string, typeof entries>();
  for (const e of entries) {
    const key = e.session_id ?? "ก่อนการเดินทาง";
    groups.set(key, [...(groups.get(key) ?? []), e]);
  }
  let sessionNo = 0;

  return (
    <>
      <SectionHeader title="บันทึกการเดินทาง" sub="เหตุการณ์ที่ปาร์ตี้รับรู้" />
      <div className="filterbar">
        <Segmented ariaLabel="กรองบันทึก" value={filter} onChange={setFilter}
          options={[
            { value: "all", label: "ทั้งหมด" },
            { value: "knowledge", label: "การค้นพบ" },
            { value: "travel", label: "การเดินทาง" },
            { value: "private", label: "ส่วนตัว" },
          ]} />
      </div>
      <Surface>
        {[...groups.entries()].map(([sid, rows]) => {
          sessionNo += 1;
          return (
            <div key={sid}>
              <div className="timeline-group">ช่วงที่ {sessionNo}</div>
              <div className="timeline">
                {rows.map((e) => (
                  <div key={e.seq} className={`timeline-entry ${e.private ? "private" : ""}`}>
                    <div className="when">
                      {e.game_time_th} · {e.event_type_th}
                      {e.private && <Chip tone="rain">เฉพาะเจ้า</Chip>}
                    </div>
                    <div className="what">{e.summary}</div>
                  </div>
                ))}
              </div>
            </div>
          );
        })}
      </Surface>
    </>
  );
}
