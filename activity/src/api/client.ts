/** Small authenticated API client. The session token lives in memory only —
 * the Activity re-authenticates through the Discord SDK on relaunch, so nothing
 * sensitive is persisted in localStorage. */

export class ApiError extends Error {
  constructor(public status: number, message: string) {
    super(message);
  }
}

export interface ReverieApi {
  get<T>(path: string): Promise<T>;
  post<T>(path: string, body?: unknown): Promise<T>;
}

export class HttpApi implements ReverieApi {
  private token: string | null = null;
  onUnauthorized: (() => void) | null = null;

  setToken(token: string | null) {
    this.token = token;
  }

  private async request<T>(method: string, path: string, body?: unknown): Promise<T> {
    const headers: Record<string, string> = {};
    if (this.token) headers["Authorization"] = `Bearer ${this.token}`;
    if (body !== undefined) headers["Content-Type"] = "application/json";
    const resp = await fetch(`/api/activity/v1${path}`, {
      method,
      headers,
      body: body !== undefined ? JSON.stringify(body) : undefined,
    });
    if (resp.status === 401) {
      this.onUnauthorized?.();
      throw new ApiError(401, "session expired");
    }
    if (!resp.ok) {
      let detail = resp.statusText;
      try {
        detail = (await resp.json()).detail ?? detail;
      } catch {
        /* keep statusText */
      }
      throw new ApiError(resp.status, detail);
    }
    return (await resp.json()) as T;
  }

  get<T>(path: string): Promise<T> {
    return this.request<T>("GET", path);
  }

  post<T>(path: string, body?: unknown): Promise<T> {
    return this.request<T>("POST", path, body);
  }
}
