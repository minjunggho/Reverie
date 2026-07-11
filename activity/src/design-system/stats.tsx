import type { BreakdownPart, ResourceView } from "../api/types";

export function StatMedallion({ label, value, gold }: {
  label: string; value: string | number; gold?: boolean;
}) {
  return (
    <div className={`medallion ${gold ? "gold" : ""}`}>
      <div className="value">{value}</div>
      <div className="label">{label}</div>
    </div>
  );
}

export function HealthBar({ hp, maxHp, tempHp = 0 }: {
  hp: number; maxHp: number; tempHp?: number;
}) {
  const pct = maxHp > 0 ? Math.max(0, Math.min(100, (hp / maxHp) * 100)) : 0;
  const tempPct = maxHp > 0 ? Math.min(100 - pct, (tempHp / maxHp) * 100) : 0;
  const tone = pct <= 33 ? "critical" : pct < 100 ? "hurt" : "";
  return (
    <div className="hpbar">
      <div className="hpbar-track" role="meter" aria-label="พลังชีวิต"
           aria-valuenow={hp} aria-valuemin={0} aria-valuemax={maxHp}>
        <div className={`hpbar-fill ${tone}`} style={{ width: `${pct}%` }} />
        {tempHp > 0 && (
          <div className="hpbar-temp" style={{ left: `${pct}%`, width: `${tempPct}%` }}
               aria-hidden />
        )}
      </div>
      <div className="hpbar-line">
        <span><strong>{hp}</strong> / {maxHp} HP</span>
        {tempHp > 0 && <span>+{tempHp} ชั่วคราว</span>}
      </div>
    </div>
  );
}

export function ResourcePips({ current, max }: { current: number; max: number }) {
  if (max > 8) {
    return <span style={{ fontVariantNumeric: "tabular-nums" }}>{current} / {max}</span>;
  }
  return (
    <span className="pips" role="img" aria-label={`${current} จาก ${max}`}>
      {Array.from({ length: max }).map((_, i) => (
        <span key={i} className={`pip ${i < current ? "full" : ""}`} />
      ))}
    </span>
  );
}

export function ResourceTracker({ resource, onOpen }: {
  resource: ResourceView; onOpen?: () => void;
}) {
  const inner = (
    <>
      <div className="name">
        <span className="th">{resource.name_th}</span>
        <span className="en">{resource.recharge_th}</span>
      </div>
      <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
        <ResourcePips current={resource.current} max={resource.max} />
        <span className="num">{resource.current}/{resource.max}</span>
      </div>
    </>
  );
  if (onOpen) {
    return (
      <button className="stat-row clickable" style={{ width: "100%", background: "none", border: "none", color: "inherit", font: "inherit" }}
              onClick={onOpen} aria-label={`${resource.name_th} ${resource.current} จาก ${resource.max}`}>
        {inner}
      </button>
    );
  }
  return <div className="stat-row">{inner}</div>;
}

export function ModifierBreakdown({ parts, total }: {
  parts: BreakdownPart[]; total: number;
}) {
  return (
    <div className="breakdown">
      {parts.map((p, i) => (
        <div className="breakdown-row" key={i}>
          <span>{p.label}</span>
          <strong>{p.value >= 0 ? `+${p.value}` : p.value}</strong>
        </div>
      ))}
      <div className="breakdown-total">
        <span>รวม</span>
        <span style={{ color: "var(--gold)" }}>{total >= 0 ? `+${total}` : total}</span>
      </div>
    </div>
  );
}

export function ConcentrationBanner({ name }: { name: string }) {
  return (
    <div className="concentration-banner" role="status">
      <span aria-hidden>◈</span>
      <span>กำลังเพ่งสมาธิ: <strong>{name}</strong> — เสียสมาธิเมื่อรับความเสียหายและเซฟไม่ผ่าน</span>
    </div>
  );
}

export function signed(n: number): string {
  return n >= 0 ? `+${n}` : `${n}`;
}
