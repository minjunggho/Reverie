/** Discord Embedded App SDK adapter.
 *
 * Three launch modes:
 *  - DISCORD:  inside the Discord Activity iframe (`frame_id` in the query).
 *              Real SDK: ready → authorize (minimal scopes) → backend exchange →
 *              authenticate. The client secret stays on the server.
 *  - MOCK:     dev-only (`?mock=1` AND a dev build) — fixtures, no network.
 *  - OUTSIDE:  a plain browser tab in production → fallback screen.
 */

export type LaunchMode = "discord" | "mock" | "outside";

export interface DiscordAuthResult {
  sessionToken: string;
  channelId: string | null;
  guildId: string | null;
}

export function detectLaunchMode(): LaunchMode {
  const params = new URLSearchParams(window.location.search);
  if (params.has("frame_id")) return "discord";
  if (import.meta.env.DEV && (params.has("mock") || import.meta.env.VITE_REVERIE_MOCK === "1")) {
    return "mock";
  }
  // Playwright screenshot runs load the built app with ?mock=1&e2e=1.
  if (params.has("mock") && params.has("e2e")) return "mock";
  return "outside";
}

export async function authenticateWithDiscord(): Promise<DiscordAuthResult> {
  const { DiscordSDK } = await import("@discord/embedded-app-sdk");
  const cfg = await fetch("/api/activity/v1/config").then((r) => r.json());
  const clientId: string | null = cfg.discord_client_id;
  if (!clientId) throw new Error("Discord Activity ยังไม่ได้ตั้งค่า (DISCORD_CLIENT_ID)");

  const sdk = new DiscordSDK(clientId);
  await sdk.ready();

  const { code } = await sdk.commands.authorize({
    client_id: clientId,
    response_type: "code",
    state: "",
    prompt: "none",
    scope: ["identify", "guilds"],
  });

  const exchanged = await fetch("/api/activity/v1/auth/exchange", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ code }),
  });
  if (!exchanged.ok) throw new Error("การยืนยันตัวตนกับ Reverie ล้มเหลว");
  const body = await exchanged.json();

  // Complete the SDK handshake; Reverie never stores this Discord token.
  await sdk.commands.authenticate({ access_token: body.discord_access_token });

  return {
    sessionToken: body.session_token,
    channelId: sdk.channelId ?? null,
    guildId: sdk.guildId ?? null,
  };
}
