import { useMemo, useState } from "react";
import { useApp } from "../app/AppContext";
import { useApi } from "../hooks/useApi";
import type { SpellView, SpellbookPayload } from "../api/types";
import { Chip, EmptyState, ErrorState, LoadingSkeleton, Sheet, Surface, SectionHeader, Segmented } from "../design-system/core";
import { ConcentrationBanner, ResourcePips, signed } from "../design-system/stats";

type Filter = "all" | "prepared" | "cantrip" | "concentration" | "ritual";

export function Spellbook() {
  const { campaignId } = useApp();
  const { data, error, loading, refresh } =
    useApi<SpellbookPayload>(`/campaigns/${campaignId}/grimoire/spellbook`);
  const [filter, setFilter] = useState<Filter>("all");
  const [open, setOpen] = useState<SpellView | null>(null);

  const spells = useMemo(() => {
    if (!data) return [];
    switch (filter) {
      case "prepared": return data.spells.filter((s) => s.prepared);
      case "cantrip": return data.spells.filter((s) => s.level === 0);
      case "concentration": return data.spells.filter((s) => s.concentration);
      case "ritual": return data.spells.filter((s) => s.ritual);
      default: return data.spells;
    }
  }, [data, filter]);

  if (loading) return <LoadingSkeleton rows={5} />;
  if (error) return <ErrorState message={error} onRetry={refresh} />;
  if (!data) return null;
  if (!data.is_caster) {
    return <EmptyState glyph="❋" title="ตัวละครนี้ไม่ใช่ผู้ใช้เวท" />;
  }

  const byLevel = new Map<number, SpellView[]>();
  for (const s of spells) {
    byLevel.set(s.level, [...(byLevel.get(s.level) ?? []), s]);
  }

  return (
    <>
      {data.concentration && <ConcentrationBanner name={data.concentration.name} />}

      <Surface>
        <div style={{ display: "flex", flexWrap: "wrap", gap: "10px 26px", alignItems: "center" }}>
          <div><span style={{ color: "var(--text-3)", fontSize: 13 }}>Save DC</span>{" "}
            <strong style={{ fontSize: 20, color: "var(--gold)" }}>{data.save_dc}</strong></div>
          <div><span style={{ color: "var(--text-3)", fontSize: 13 }}>โจมตีเวท</span>{" "}
            <strong style={{ fontSize: 20 }}>{signed(data.attack_bonus ?? 0)}</strong></div>
          <div><span style={{ color: "var(--text-3)", fontSize: 13 }}>ใช้ค่า</span>{" "}
            <strong>{data.ability?.toUpperCase()}</strong></div>
          {data.slots.map((sl) => (
            <div key={sl.level} style={{ display: "flex", alignItems: "center", gap: 8 }}>
              <span style={{ color: "var(--text-3)", fontSize: 13 }}>ช่องเวท Lv.{sl.level}</span>
              <ResourcePips current={sl.current} max={sl.max} />
            </div>
          ))}
        </div>
      </Surface>

      <div className="filterbar" style={{ marginTop: 16 }}>
        <Segmented ariaLabel="กรองคาถา" value={filter} onChange={setFilter}
          options={[
            { value: "all", label: "ทั้งหมด" },
            { value: "prepared", label: "เตรียมไว้" },
            { value: "cantrip", label: "คาถาประจำตัว" },
            { value: "concentration", label: "เพ่งสมาธิ" },
            { value: "ritual", label: "พิธีกรรม" },
          ]} />
      </div>

      {[...byLevel.entries()].sort(([a], [b]) => a - b).map(([level, rows]) => (
        <div key={level}>
          <SectionHeader title={level === 0 ? "คาถาประจำตัว" : `คาถาระดับ ${level}`}
                         sub={level === 0 ? "ร่ายได้ไม่จำกัด" : undefined} />
          <Surface tight>
            {rows.map((s) => (
              <div key={`${s.key}-${s.kind}`} className="stat-row clickable" role="button" tabIndex={0}
                   onClick={() => setOpen(s)}
                   onKeyDown={(e) => (e.key === "Enter" || e.key === " ") && setOpen(s)}
                   aria-label={`${s.name_th} — ดูรายละเอียด`}>
                <div className="name" style={{ flexWrap: "wrap" }}>
                  <span className="th">{s.name_th}</span>
                  <span className="en">{s.name}</span>
                  {s.prepared && s.level > 0 && <Chip tone="gold">เตรียมไว้</Chip>}
                  {s.concentration && <Chip tone="rain">เพ่งสมาธิ</Chip>}
                  {s.ritual && <Chip>พิธีกรรม</Chip>}
                </div>
                <span style={{ color: "var(--text-3)", fontSize: 12.5, whiteSpace: "nowrap" }}>
                  {s.category}
                </span>
              </div>
            ))}
          </Surface>
        </div>
      ))}

      <p style={{ color: "var(--text-3)", fontSize: 13, marginTop: 16 }}>
        เปลี่ยนคาถาที่เตรียมไว้ได้หลังพักยาว (ผ่านการเล่นใน Discord)
      </p>

      {open && (
        <Sheet title={`${open.name_th} (${open.name})`} onClose={() => setOpen(null)}>
          <div style={{ display: "flex", gap: 8, flexWrap: "wrap", marginBottom: 10 }}>
            <Chip>{open.level === 0 ? "คาถาประจำตัว" : `ระดับ ${open.level}`}</Chip>
            <Chip>{open.school}</Chip>
            {open.concentration && <Chip tone="rain">เพ่งสมาธิ</Chip>}
            {open.ritual && <Chip>พิธีกรรม</Chip>}
            {open.prepared && open.level > 0 && <Chip tone="gold">เตรียมไว้</Chip>}
          </div>
          <p>{open.summary_th}</p>
          <dl className="kv" style={{ marginTop: 12 }}>
            <dt>เวลาร่าย</dt><dd>{open.casting_time}</dd>
            <dt>ระยะ</dt><dd>{open.range}</dd>
            <dt>ระยะเวลา</dt><dd>{open.duration}</dd>
            <dt>ที่มา</dt><dd>{open.source}</dd>
          </dl>
        </Sheet>
      )}
    </>
  );
}
