import { useState } from "react";
import { useApp } from "../app/AppContext";
import { useApi } from "../hooks/useApi";
import type { ImportsPayload } from "../api/types";
import { Chip, ConfirmDialog, EmptyState, ErrorState, LoadingSkeleton, Surface, SectionHeader, useToast } from "../design-system/core";

type PendingAction = { importId: string; action: "approve" | "reject" | "repair" } | null;

const ACTION_TH = {
  approve: { title: "ยืนยันโลกของแคมเปญ", body: "ทุกอย่างในไฟล์นี้จะกลายเป็น canon — สถานที่ NPC ความลับ และกฎทั้งหมด การกระทำนี้ย้อนกลับไม่ได้", label: "ยืนยันเป็น canon" },
  reject: { title: "ปฏิเสธการนำเข้า", body: "ไฟล์นี้จะถูกปฏิเสธ และจะไม่มีอะไรกลายเป็น canon", label: "ปฏิเสธ" },
  repair: { title: "เติมกฎ (Protocol) ที่ขาด", body: "ระบบจะอ่านเฉพาะส่วน Protocol จากไฟล์นี้ และเพิ่มเฉพาะที่ยังไม่มี — ไม่แตะสถานที่ NPC หรือความลับเดิม", label: "เติมกฎ" },
} as const;

export function ImportsView() {
  const { campaignId, api } = useApp();
  const { data, error, loading, refresh } =
    useApi<ImportsPayload>(`/campaigns/${campaignId}/studio/imports`);
  const [pending, setPending] = useState<PendingAction>(null);
  const [busy, setBusy] = useState(false);
  const toast = useToast();

  const run = async () => {
    if (!pending) return;
    setBusy(true);
    try {
      const result = await api.post<{ status: string; protocols_added?: number }>(
        `/campaigns/${campaignId}/studio/imports/${pending.importId}/${pending.action}`);
      toast(
        result.status === "APPROVED" ? "โลกของแคมเปญถูกยืนยันเป็น canon แล้ว"
          : result.status === "REJECTED" ? "ปฏิเสธการนำเข้าแล้ว — ไม่มีอะไรถูกสร้าง"
          : `เติมกฎแล้ว ${result.protocols_added ?? 0} ชุด`,
        "success");
      await refresh();
    } catch (e) {
      toast(e instanceof Error ? e.message : "เกิดข้อผิดพลาด", "error");
    } finally {
      setBusy(false);
      setPending(null);
    }
  };

  if (loading) return <LoadingSkeleton rows={3} />;
  if (error) return <ErrorState message={error} onRetry={refresh} />;
  if (!data || data.imports.length === 0) {
    return <EmptyState glyph="⇪" title="ยังไม่มีการนำเข้าแคมเปญ"
                       hint={<>อัปโหลดไฟล์ Markdown ใน Discord ด้วย <code>!rv campaign import</code></>} />;
  }

  return (
    <>
      <SectionHeader title="การนำเข้าแคมเปญ" sub="ไม่มีอะไรเป็น canon จนกว่าเจ้าของจะยืนยัน" />
      {data.imports.map((imp) => (
        <Surface key={imp.id}>
          <div style={{ display: "flex", justifyContent: "space-between", gap: 10, flexWrap: "wrap" }}>
            <div>
              <h3 style={{ fontSize: 15.5 }}>{imp.filename}</h3>
              <div style={{ color: "var(--text-3)", fontSize: 13 }}>
                โดย {imp.uploader} · {imp.uploaded_at?.slice(0, 10) ?? "—"} · sha {imp.content_sha256}
              </div>
            </div>
            <Chip tone={imp.status === "APPROVED" ? "success"
                        : imp.status === "PENDING_REVIEW" ? "gold" : "condition"}>
              {imp.status === "PENDING_REVIEW" ? "รอการยืนยัน"
                : imp.status === "APPROVED" ? "ยืนยันแล้ว" : "ถูกปฏิเสธ"}
            </Chip>
          </div>

          <div style={{ display: "flex", gap: 6, flexWrap: "wrap", marginTop: 10 }}>
            {Object.entries(imp.counts).filter(([, v]) => v > 0).map(([k, v]) => (
              <Chip key={k}>{k}: {v}</Chip>
            ))}
          </div>

          {imp.warnings.length > 0 && (
            <div className="warn-box">
              {imp.warnings.map((w, i) => <div key={i}>⚠ {w}</div>)}
            </div>
          )}

          <div style={{ display: "flex", gap: 8, marginTop: 12, flexWrap: "wrap" }}>
            {imp.status === "PENDING_REVIEW" ? (
              <>
                <button className="btn primary"
                        onClick={() => setPending({ importId: imp.id, action: "approve" })}>
                  ยืนยันเป็น canon
                </button>
                <button className="btn danger"
                        onClick={() => setPending({ importId: imp.id, action: "reject" })}>
                  ปฏิเสธ
                </button>
                <button className="btn"
                        onClick={() => setPending({ importId: imp.id, action: "repair" })}>
                  เติมกฎที่ขาด (repair)
                </button>
              </>
            ) : (
              <span style={{ color: "var(--text-3)", fontSize: 13 }}>
                {imp.status === "APPROVED"
                  ? "เป็น canon แล้ว — แก้ไขผ่านการนำเข้าไฟล์ฉบับปรับปรุง + repair เท่านั้น"
                  : "ไม่มีอะไรถูกสร้างจากไฟล์นี้"}
              </span>
            )}
          </div>
        </Surface>
      ))}

      {pending && (
        <ConfirmDialog
          title={ACTION_TH[pending.action].title}
          body={ACTION_TH[pending.action].body}
          confirmLabel={ACTION_TH[pending.action].label}
          danger={pending.action === "reject"}
          busy={busy}
          onConfirm={run}
          onCancel={() => setPending(null)}
        />
      )}
    </>
  );
}
