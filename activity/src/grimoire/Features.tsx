import { useApp } from "../app/AppContext";
import { useApi } from "../hooks/useApi";
import type { FeaturesPayload } from "../api/types";
import { Chip, EmptyState, ErrorState, LoadingSkeleton, Surface, SectionHeader } from "../design-system/core";
import { ResourcePips } from "../design-system/stats";

export function Features() {
  const { campaignId } = useApp();
  const { data, error, loading, refresh } =
    useApi<FeaturesPayload>(`/campaigns/${campaignId}/grimoire/features`);

  if (loading) return <LoadingSkeleton rows={5} />;
  if (error) return <ErrorState message={error} onRetry={refresh} />;
  if (!data || data.groups.length === 0) {
    return <EmptyState title="ยังไม่มีความสามารถพิเศษ" />;
  }

  return (
    <>
      {data.groups.map((g) => (
        <div key={g.source_type}>
          <SectionHeader title={g.source_th} sub={g.source_type} />
          <Surface tight>
            {g.entries.map((e) => (
              <div key={`${e.grant_type}-${e.key}`} className="stat-row"
                   style={{ alignItems: "flex-start", flexDirection: "column", gap: 4 }}>
                <div style={{ display: "flex", gap: 8, alignItems: "center", flexWrap: "wrap", width: "100%" }}>
                  <span className="th" style={{ fontWeight: 550 }}>{e.name_th}</span>
                  {e.resource && (
                    <span style={{ display: "inline-flex", gap: 6, alignItems: "center", marginLeft: "auto" }}>
                      <ResourcePips current={e.resource.current} max={e.resource.max} />
                      <span style={{ fontSize: 12, color: "var(--text-3)" }}>{e.resource.recharge_th}</span>
                    </span>
                  )}
                </div>
                <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
                  {e.executable
                    ? (e.resource
                        ? <Chip tone={e.resource.current > 0 ? "success" : "condition"}>
                            {e.resource.current > 0 ? "พร้อมใช้" : `ใช้หมดแล้ว — กลับมาหลัง${e.resource.recharge_th}`}
                          </Chip>
                        : <Chip tone="success">พร้อมใช้</Chip>)
                    : <Chip>บันทึกไว้ แต่กลไกยังไม่รองรับ</Chip>}
                  <span style={{ fontSize: 12, color: "var(--text-3)" }}>{e.source_key}</span>
                </div>
              </div>
            ))}
          </Surface>
        </div>
      ))}
    </>
  );
}
