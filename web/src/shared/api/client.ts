import { config } from '../config';
import { endSession, getIdToken, refreshSession, sessionStore } from '../auth/session';

/**
 * A failed call. `status` is the HTTP status (0: the request never got an
 * answer); `message` is the backend's own, in English: `{"message": ...}` for
 * business errors (shared/errors.py) and API Gateway's refusals, or the first
 * item of `detail` for a 422.
 */
export class ApiError extends Error {
  readonly status: number;

  constructor(status: number, message: string) {
    super(message);
    this.name = 'ApiError';
    this.status = status;
  }
}

export interface RequestOptions {
  method?: 'GET' | 'POST' | 'PATCH' | 'DELETE';
  body?: unknown;
  query?: Record<string, string | number | null | undefined>;
  signal?: AbortSignal;
}

function urlOf(path: string, query: RequestOptions['query']): string {
  const params = new URLSearchParams();
  for (const [key, value] of Object.entries(query ?? {})) {
    if (value !== undefined && value !== null) params.set(key, String(value));
  }
  const search = params.toString();
  return `${config.apiUrl}${path}${search ? `?${search}` : ''}`;
}

async function send(path: string, options: RequestOptions): Promise<Response> {
  const token = await getIdToken();
  if (!token) {
    await endSession('expired');
    throw new ApiError(401, 'Not signed in');
  }
  const headers: Record<string, string> = { Authorization: `Bearer ${token}` };
  // The workspace this tab works in: the backend checks the token lists it.
  const tenantId = sessionStore.getState().user?.tenantId;
  if (tenantId) headers['X-Tenant-Id'] = tenantId;
  if (options.body !== undefined) headers['Content-Type'] = 'application/json';
  try {
    return await fetch(urlOf(path, options.query), {
      method: options.method ?? 'GET',
      headers,
      body: options.body === undefined ? undefined : JSON.stringify(options.body),
      signal: options.signal,
    });
  } catch (error) {
    if (options.signal?.aborted) throw error;
    throw new ApiError(0, 'Network error');
  }
}

async function errorOf(response: Response): Promise<ApiError> {
  const body: unknown = await response.json().catch(() => null);
  let message = response.statusText || `HTTP ${response.status}`;
  if (body && typeof body === 'object') {
    const { message: text, detail } = body as { message?: unknown; detail?: unknown };
    if (typeof text === 'string') message = text;
    else if (Array.isArray(detail) && typeof detail[0]?.msg === 'string') message = detail[0].msg;
  }
  return new ApiError(response.status, message);
}

/**
 * Calls the HTTP API with the user's ID token. A 401 gets one retry with fresh
 * tokens (the token may predate a workspace change); a second one, or a refresh
 * that fails because the session was revoked, signs the user out.
 */
export async function api<T>(path: string, options: RequestOptions = {}): Promise<T> {
  let response = await send(path, options);
  if (response.status === 401) {
    if (!(await refreshSession())) throw new ApiError(401, 'Signed out');
    response = await send(path, options);
    if (response.status === 401) {
      await endSession('expired');
      throw await errorOf(response);
    }
  }
  if (!response.ok) throw await errorOf(response);
  if (response.status === 204) return undefined as T;
  return (await response.json()) as T;
}
