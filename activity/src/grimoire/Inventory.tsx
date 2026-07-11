import { useMemo, useState } from "react";
import { useApp } from "../app/AppContext";
import { useApi } from "../hooks/useApi";
import type { InventoryPayload } from "../api/types";
import { Chip, EmptyState, ErrorState, LoadingSkeleton, Surface, SectionHeader } from "../design-system/core";

const KIND_TH: Record<string, string> = {
  weapon: "อาวุธ", armor: "เกราะ", gear: "อุปกรณ์", consumable: "ของใช้", treasure: "ทรัพย์",
};

export function Inventory() {
  const { campaignId } = useApp();
  const { data, error, loading, refresh } =
    useApi<InventoryPayload>(`/campaigns/${campaignId}/grimoire/inventory`);
  const [q, setQ] = useState("");
  const [kind, setKind] = useState<string>("all");

  const items = useMemo(() => {
    if (!data) return [];
    return data.items.filter((i) =>
      (kind === "all" || i.kind === kind) &&
      (!q || i.name.toLowerCase().includes(q.toLowerCase())));
  }, [data, q, kind]);

  if (loading) return <LoadingSkeleton rows={4} />;
  if (error) return <ErrorState message={error} onRetry={refresh} />;
  if (!data || data.count === 0) {
    return <EmptyState glyph="◇" title="ย่ามว่างเปล่า"
                       hint="โลกยังไม่ได้มอบอะไรให้ — ออกผจญภัยใน Discord ก่อน" />;
  }

  const kinds = ["all", ...new Set(data.items.map((i) => i.kind))];

  return (
    <>
      <SectionHeader title="สัมภาระ" sub={`${data.count} รายการ`} />
      <div className="filterbar">
        <input className="searchfield" style={{ maxWidth: 280 }} value={q}
               onChange={(e) => setQ(e.target.value)}
               placeholder="ค้นหาไอเทม…" aria-label="ค้นหาไอเทม" />
        <div className="segmented" role="tablist" aria-label="ประเภทไอเทม">
          {kinds.map((k) => (
            <button key={k} role="tab" aria-selected={kind === k}
                    className={kind === k ? "active" : ""} onClick={() => setKind(k)}>
              {k === "all" ? "ทั้งหมด" : KIND_TH[k] ?? k}
            </button>
          ))}
        </div>
      </div>
      <Surface tight>
        {items.map((i) => (
          <div key={i.id} className="stat-row" style={{ alignItems: "flex-start" }}>
            <div className="name" style={{ flexDirection: "column", alignItems: "flex-start", gap: 2 }}>
              <span>
                <span className="th" style={{ fontWeight: 550 }}>{i.name}</span>
                {i.quantity > 1 && <span style={{ color: "var(--text-3)" }}> ×{i.quantity}</span>}
              </span>
              {i.description && (
                <span style={{ color: "var(--text-3)", fontSize: 13 }}>{i.description}</span>
              )}
            </div>
            <div style={{ display: "flex", gap: 6, flexShrink: 0 }}>
              {i.equipped && <Chip tone="gold">สวมใส่อยู่</Chip>}
              <Chip>{KIND_TH[i.kind] ?? i.kind}</Chip>
            </div>
          </div>
        ))}
        {items.length === 0 && (
          <EmptyState title="ไม่พบไอเทมที่ตรงกับตัวกรอง" />
        )}
      </Surface>
    </>
  );
}
