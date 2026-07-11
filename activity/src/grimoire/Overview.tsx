import { useState } from "react";
import { useApp } from "../app/AppContext";
import { useApi } from "../hooks/useApi";
import type { Overview as OverviewT, ResourceView } from "../api/types";
import { EmptyState, ErrorState, LoadingSkeleton, Sheet, Surface, SectionHeader, Chip } from "../design-system/core";
import { ConcentrationBanner, HealthBar, ResourceTracker, StatMedallion, signed } from "../design-system/stats";

export function Overview() {
  const { campaignId } = useApp();
  const { data, error, loading, refresh } =
    useApi<OverviewT>(`/campaigns/${campaignId}/grimoire/overview`);
  const [openResource, setOpenResource] = useState<ResourceView | null>(null);

  if (loading) return <LoadingSkeleton rows={5} />;
  if (error) return <ErrorState message={error} onRetry={refresh} />;
  if (!data) return <EmptyState title="ยังไม่มีตัวละครในแคมเปญนี้"
    hint={<>เริ่มสร้างตัวละครใน Discord ด้วย <code>!rv character</code></>} />;

  return (
    <>
      <div style={{ display: "flex", alignItems: "baseline", gap: 12, flexWrap: "wrap" }}>
        <h1 style={{ fontFamily: "var(--font-display)", fontSize: 26, color: "var(--gold)" }}>
          {data.name}
        </h1>
        <span style={{ color: "var(--text-2)" }}>
          {data.species_name_th} · {data.class_name_th} เลเวล {data.level}
          {data.background_name_th && ` · ${data.background_name_th}`}
        </span>
      </div>
      {data.concept && (
        <p style={{ color: "var(--text-3)", fontStyle: "italic", marginTop: 2 }}>{data.concept}</p>
      )}
      <p style={{ color: "var(--text-3)", fontSize: 13 }}>
        {data.location_name && <>อยู่ที่ <strong style={{ color: "var(--rain)" }}>{data.location_name}</strong> · </>}
        {data.game_time_th}
      </p>

      {data.concentration && <ConcentrationBanner name={data.concentration.name} />}

      <Surface>
        <HealthBar hp={data.hp} maxHp={data.max_hp} tempHp={data.temp_hp} />
        {(data.conditions.length > 0 || data.exhaustion > 0 || data.dying || data.stable) && (
          <div style={{ display: "flex", gap: 8, flexWrap: "wrap", marginTop: 10 }}>
            {data.dying && <Chip tone="condition">กำลังจะตาย · เซฟ {data.death_saves.successes}✓ {data.death_saves.failures}✗</Chip>}
            {data.stable && <Chip tone="success">ทรงตัวแล้ว</Chip>}
            {data.conditions.map((c) => <Chip key={c} tone="condition">{c}</Chip>)}
            {data.exhaustion > 0 && <Chip tone="condition">อ่อนล้าระดับ {data.exhaustion}</Chip>}
          </div>
        )}
      </Surface>

      <SectionHeader title="ค่าประจำตัว" />
      <div className="medallion-row">
        <StatMedallion label="AC" value={data.ac} gold />
        <StatMedallion label="Initiative" value={signed(data.initiative)} />
        <StatMedallion label="ความเร็ว" value={`${data.speed} ฟุต`} />
        <StatMedallion label="Proficiency" value={signed(data.proficiency_bonus)} />
        <StatMedallion label="Hit Dice" value={`${data.hit_dice_remaining}d${data.hit_die}`} />
        {data.spellcasting && (
          <StatMedallion label="Spell DC" value={data.spellcasting.save_dc} gold />
        )}
      </div>

      {data.resources.length > 0 && (
        <>
          <SectionHeader title="ทรัพยากร" sub="แตะเพื่อดูที่มา" />
          <Surface tight>
            {data.resources.map((r) => (
              <ResourceTracker key={r.resource_id} resource={r}
                               onOpen={() => setOpenResource(r)} />
            ))}
          </Surface>
        </>
      )}

      {openResource && (
        <Sheet title={openResource.name_th} onClose={() => setOpenResource(null)}>
          <dl className="kv">
            <dt>คงเหลือ</dt><dd>{openResource.current} / {openResource.max}</dd>
            <dt>ฟื้นคืนเมื่อ</dt><dd>{openResource.recharge_th || "—"}</dd>
            <dt>ชื่อกลไก</dt><dd>{openResource.name}</dd>
          </dl>
          <p style={{ color: "var(--text-3)", fontSize: 13, marginTop: 12 }}>
            ตัวเลขนี้มาจากสถานะจริงของเอนจิน — ใช้ผ่านการเล่นใน Discord
          </p>
        </Sheet>
      )}
    </>
  );
}
