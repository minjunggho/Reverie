/** Frontend unit tests — mock API, real components. */
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { ReactNode } from "react";
import { describe, expect, it } from "vitest";
import { MockApi } from "../api/mockClient";
import { mockContext } from "../mocks/fixtures";
import { AppProvider } from "../app/AppContext";
import { ToastProvider } from "../design-system/core";
import { AppShell } from "../components/AppShell";
import { Overview } from "../grimoire/Overview";
import { Skills } from "../grimoire/Skills";
import { Spellbook } from "../grimoire/Spellbook";
import { ImportsView } from "../dm-studio/ImportsView";
import { PermissionDenied } from "../design-system/core";

function wrap(children: ReactNode, opts: { role?: string; view?: string } = {}) {
  const context = {
    ...mockContext,
    membership: opts.role === "PLAYER"
      ? { role: "PLAYER", can_open_dm_studio: false }
      : mockContext.membership,
  };
  const value = {
    api: new MockApi(),
    context,
    campaignId: "camp-1",
    refreshContext: async () => {},
    view: opts.view ?? "grimoire/overview",
    navigate: () => {},
  };
  return render(
    <ToastProvider>
      <AppProvider value={value}>{children}</AppProvider>
    </ToastProvider>,
  );
}

describe("Grimoire Overview", () => {
  it("renders name, HP, AC, conditions and concentration", async () => {
    wrap(<Overview />);
    expect(await screen.findByText("Daybell")).toBeInTheDocument();
    expect(screen.getByText("4")).toBeInTheDocument();          // HP current
    expect(screen.getByRole("meter", { name: "พลังชีวิต" })).toHaveAttribute("aria-valuenow", "4");
    expect(screen.getByText("หวาดกลัว")).toBeInTheDocument();    // condition chip
    expect(screen.getByRole("status")).toHaveTextContent("Detect Magic"); // concentration
  });

  it("resource tracker is accessible and opens provenance", async () => {
    wrap(<Overview />);
    const tracker = await screen.findByRole("button", { name: /ฟื้นพลังเวท 1 จาก 1/ });
    await userEvent.click(tracker);
    const dialog = await screen.findByRole("dialog", { name: "ฟื้นพลังเวท" });
    expect(within(dialog).getByText("พักยาว")).toBeInTheDocument();
    expect(within(dialog).getByText("1 / 1")).toBeInTheDocument();
  });
});

describe("Skills", () => {
  it("opens a real backend breakdown for a skill", async () => {
    wrap(<Skills />);
    const arcana = await screen.findByRole("button", { name: /ศาสตร์เวท \+5/ });
    await userEvent.click(arcana);
    const dialog = await screen.findByRole("dialog");
    expect(dialog).toHaveTextContent("INT");
    expect(dialog).toHaveTextContent("Proficiency");
    expect(dialog).toHaveTextContent("+5");
  });

  it("filters to proficient-only", async () => {
    wrap(<Skills />);
    await screen.findByText("ศาสตร์เวท");
    expect(screen.getByText("ย่องเงียบ")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "เฉพาะที่ถนัด" }));
    expect(screen.queryByText("ย่องเงียบ")).not.toBeInTheDocument();
    expect(screen.getByText("ศาสตร์เวท")).toBeInTheDocument();
  });
});

describe("Spellbook", () => {
  it("shows concentration banner, slots, and filters prepared", async () => {
    wrap(<Spellbook />);
    const banner = await screen.findByText(/กำลังเพ่งสมาธิ/);
    expect(banner).toHaveTextContent("Detect Magic");
    expect(screen.getByText("Save DC")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("tab", { name: "เตรียมไว้" }));
    expect(screen.queryByText("มนตร์หลับ")).not.toBeInTheDocument();  // unprepared
    expect(screen.getByText("โล่เวท")).toBeInTheDocument();           // prepared
  });
});

describe("Surface switching & authorization UI", () => {
  it("owners see the DM Studio switch", () => {
    wrap(<AppShell>x</AppShell>);
    expect(screen.getByTestId("topbar-switch")).toHaveTextContent("DM Studio");
  });

  it("players never see the DM Studio switch", () => {
    wrap(<AppShell>x</AppShell>, { role: "PLAYER" });
    expect(screen.queryByTestId("topbar-switch")).not.toBeInTheDocument();
    expect(screen.queryByTestId("surface-switch")).not.toBeInTheDocument();
  });

  it("permission-denied state renders", () => {
    wrap(<PermissionDenied />);
    expect(screen.getByRole("alert"))
      .toHaveTextContent("ข้อมูลนี้เปิดให้เฉพาะเจ้าของแคมเปญหรือ DM");
  });

  it("mobile bottom navigation lists the primary tabs", () => {
    wrap(<AppShell>x</AppShell>);
    const nav = screen.getByRole("navigation", { name: "เมนูล่าง" });
    expect(nav).toHaveTextContent("ภาพรวม");
    expect(nav).toHaveTextContent("คาถา");
    expect(nav).toHaveTextContent("บันทึก");
  });
});

describe("DM Studio imports", () => {
  it("approve flows through confirmation and reports success", async () => {
    wrap(<ImportsView />, { view: "studio/imports" });
    const approve = await screen.findByRole("button", { name: "ยืนยันเป็น canon" });
    await userEvent.click(approve);
    const dialog = await screen.findByRole("alertdialog");
    expect(dialog).toHaveTextContent("ย้อนกลับไม่ได้");
    await userEvent.click(within(dialog).getByRole("button", { name: "ยืนยันเป็น canon" }));
    await waitFor(() =>
      expect(screen.getByRole("status")).toHaveTextContent("canon"));
  });
});
