import { useMemo, useState } from "react";
import { useApp } from "../app/AppContext";
import { useApi } from "../hooks/useApi";
import type { SkillView, SkillsPayload } from "../api/types";
import { ErrorState, LoadingSkeleton, Sheet, Surface, SectionHeader, Segmented } from "../design-system/core";
import { ModifierBreakdown, signed } from "../design-system/stats";

type SortKey = "name" | "total";

export function Skills() {
  const { campaignId } = useApp();
  const { data, error, loading, refresh } =
    useApi<SkillsPayload>(`/campaigns/${campaignId}/grimoire/skills`);
  const [sort, setSort] = useState<SortKey>("name");
  const [profOnly, setProfOnly] = useState(false);
  const [open, setOpen] = useState<SkillView | null>(null);

  const skills = useMemo(() => {
    if (!data) return [];
    let rows = [...data.skills];
    if (profOnly) rows = rows.filter((s) => s.proficiency !== "NONE");
    rows.sort((a, b) => sort === "total"
      ? b.total - a.total || a.name_th.localeCompare(b.name_th, "th")
      : a.name_th.localeCompare(b.name_th, "th"));
    return rows;
  }, [data, sort, profOnly]);

  if (loading) return <LoadingSkeleton rows={6} />;
  if (error) return <ErrorState message={error} onRetry={refresh} />;
  if (!data) return null;

  return (
    <>
      <SectionHeader title="ความสามารถ" sub={`Proficiency ${signed(data.proficiency_bonus)}`} />
      <div className="medallion-row">
        {data.abilities.map((a) => (
          <div key={a.key} className="medallion">
            <div className="value">{a.score}</div>
            <div className="label">{a.key.toUpperCase()} {signed(a.modifier)}</div>
            <div style={{ fontSize: 12, color: a.save_proficient ? "var(--gold)" : "var(--text-3)", marginTop: 4 }}>
              เซฟ {signed(a.save_total)}{a.save_proficient ? " ●" : ""}
            </div>
          </div>
        ))}
      </div>

      <SectionHeader title="ทักษะ" sub={`Passive Perception ${data.passive_perception}`} />
      <div className="filterbar">
        <Segmented ariaLabel="เรียงลำดับ" value={sort} onChange={setSort}
          options={[{ value: "name", label: "ตามชื่อ" }, { value: "total", label: "ตามโบนัส" }]} />
        <button className={`btn small ${profOnly ? "primary" : ""}`}
                aria-pressed={profOnly}
                onClick={() => setProfOnly(!profOnly)}>
          เฉพาะที่ถนัด
        </button>
      </div>
      <Surface tight>
        {skills.map((s) => (
          <div key={s.key} className="stat-row clickable" role="button" tabIndex={0}
               onClick={() => setOpen(s)}
               onKeyDown={(e) => (e.key === "Enter" || e.key === " ") && setOpen(s)}
               aria-label={`${s.name_th} ${signed(s.total)} — ดูที่มา`}>
            <div className="name">
              <span className="th">{s.name_th}</span>
              <span className="en">{s.name} · {s.ability.toUpperCase()}</span>
              {s.proficiency !== "NONE" && (
                <span className={`badge-prof ${s.proficiency}`}>
                  {s.proficiency === "EXPERTISE" ? "เชี่ยวชาญ" : "ถนัด"}
                </span>
              )}
            </div>
            <span className="num">{signed(s.total)}</span>
          </div>
        ))}
      </Surface>

      {open && (
        <Sheet title={`${open.name_th} ${signed(open.total)}`} onClose={() => setOpen(null)}>
          <p style={{ color: "var(--text-2)" }}>{open.explain_th}</p>
          <ModifierBreakdown parts={open.breakdown} total={open.total} />
          <dl className="kv" style={{ marginTop: 10 }}>
            <dt>ใช้ค่า</dt><dd>{open.ability.toUpperCase()}</dd>
            <dt>Passive</dt><dd>{open.passive}</dd>
          </dl>
        </Sheet>
      )}
    </>
  );
}
