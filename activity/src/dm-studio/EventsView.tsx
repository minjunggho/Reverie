import { useState } from "react";
import { useApp } from "../app/AppContext";
import { useApi } from "../hooks/useApi";
import type { StudioEvent } from "../api/types";
import { EmptyState, ErrorState, LoadingSkeleton, Surface, SectionHeader, Segmented, VisBadge } from "../design-system/core";

type VisFilter = "all" | "PUBLIC" | "PARTY" | "PLAYER_ONLY" | "DM_ONLY";

export function EventsView() {
  const { campaignId } = useApp();
  const [vis, setVis] = useState<VisFilter>("all");
  const q = vis === "all" ? "" : `&visibility=${vis}`;
  const { data, error, loading, refresh } =
    useApi<{ total: number; events: StudioEvent[] }>(
      `/campaigns/${campaignId}/studio/events?limit=50${q}`);
  const [openSeq, setOpenSeq] = useState<number | null>(null);

  if (loading) return <LoadingSkeleton rows={6} />;
  if (error) return <ErrorState message={error} onRetry={refresh} />;
  if (!data) return null;

  return (
    <>
      <SectionHeader title="ตัวตรวจสอบเหตุการณ์" sub={`ทั้งหมด ${data.total} เหตุการณ์`} />
      <div className="filterbar">
        <Segmented ariaLabel="กรองตามการมองเห็น" value={vis} onChange={setVis}
          options={[
            { value: "all", label: "ทั้งหมด" },
            { value: "PARTY", label: "PARTY" },
            { value: "PUBLIC", label: "PUBLIC" },
            { value: "PLAYER_ONLY", label: "PLAYER" },
            { value: "DM_ONLY", label: "DM" },
          ]} />
      </div>
      {data.events.length === 0 && <EmptyState title="ไม่มีเหตุการณ์ตามตัวกรอง" />}
      <Surface tight>
        {[...data.events].reverse().map((e) => (
          <div key={e.seq}>
            <div className="stat-row clickable" role="button" tabIndex={0}
                 onClick={() => setOpenSeq(openSeq === e.seq ? null : e.seq)}
                 onKeyDown={(ev) => (ev.key === "Enter" || ev.key === " ") &&
                   setOpenSeq(openSeq === e.seq ? null : e.seq)}
                 aria-expanded={openSeq === e.seq}>
              <div className="name" style={{ flexDirection: "column", alignItems: "flex-start", gap: 1, minWidth: 0 }}>
                <span className="th" style={{ overflow: "hidden", textOverflow: "ellipsis", maxWidth: "100%" }}>
                  {e.summary || e.event_type}
                </span>
                <span className="en">#{e.seq} · {e.event_type} · {e.game_time_th}</span>
              </div>
              <VisBadge visibility={e.visibility} />
            </div>
            {openSeq === e.seq && (
              <div style={{ padding: "4px 8px 12px", borderBottom: "1px solid var(--border-soft)" }}>
                <dl className="kv" style={{ fontSize: 13 }}>
                  <dt>ผู้กระทำ</dt><dd>{e.actor ?? "—"}</dd>
                  <dt>เป้าหมาย</dt><dd>{e.targets.join(", ") || "—"}</dd>
                  <dt>เวลาจริง</dt><dd>{e.real_time ?? "—"}</dd>
                  <dt>ความสำคัญ</dt><dd>{e.significance}</dd>
                  {Object.keys(e.mechanical_changes).length > 0 && (
                    <>
                      <dt>กลไก</dt>
                      <dd><code style={{ fontSize: 12 }}>{JSON.stringify(e.mechanical_changes)}</code></dd>
                    </>
                  )}
                </dl>
              </div>
            )}
          </div>
        ))}
      </Surface>
    </>
  );
}
