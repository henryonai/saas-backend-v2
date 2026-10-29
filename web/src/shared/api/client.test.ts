import { beforeEach, describe, expect, it, vi } from 'vitest';
import { json } from '@/test/render';

const session = vi.hoisted(() => ({
  getIdToken: vi.fn(),
  refreshSession: vi.fn(),
  endSession: vi.fn(),
  sessionStore: { getState: vi.fn() },
}));
vi.mock('../auth/session', () => session);
vi.mock('../config', () => ({ config: { apiUrl: 'https://api.test' } }));

const { api, ApiError } = await import('./client');

const fetchMock = vi.fn<typeof fetch>();

beforeEach(() => {
  vi.stubGlobal('fetch', fetchMock);
  fetchMock.mockReset();
  session.getIdToken.mockReset().mockResolvedValue('id-token');
  session.refreshSession.mockReset().mockResolvedValue({ userId: 'u' });
  session.endSession.mockReset().mockResolvedValue(undefined);
  session.sessionStore.getState.mockReset().mockReturnValue({ user: { tenantId: 'tenant-1' } });
});

describe('api', () => {
  it('sends the ID token, the JSON body and the query', async () => {
    fetchMock.mockResolvedValue(json({ order_id: 'o-1' }, 201));
    const order = await api('/orders', { method: 'POST', body: { item: 'Tea' }, query: { limit: 5, cursor: undefined } });

    expect(order).toEqual({ order_id: 'o-1' });
    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toBe('https://api.test/orders?limit=5');
    expect(init?.method).toBe('POST');
    expect(init?.headers).toMatchObject({
      Authorization: 'Bearer id-token',
      'X-Tenant-Id': 'tenant-1', // the workspace this tab works in
      'Content-Type': 'application/json',
    });
    expect(init?.body).toBe('{"item":"Tea"}');
  });

  it('names no workspace before the user has one', async () => {
    session.sessionStore.getState.mockReturnValue({ user: { tenantId: null } });
    fetchMock.mockResolvedValue(json([]));
    await api('/invitations');
    expect(fetchMock.mock.calls[0][1]?.headers).not.toHaveProperty('X-Tenant-Id');
  });

  it('returns nothing for 204', async () => {
    fetchMock.mockResolvedValue(new Response(null, { status: 204 }));
    await expect(api('/users/u-2', { method: 'DELETE' })).resolves.toBeUndefined();
  });

  it('returns nothing for a 202 with no body', async () => {
    fetchMock.mockResolvedValue(new Response(null, { status: 202 }));
    await expect(api('/admin/tenants/t1/suspend', { method: 'POST', body: {} })).resolves.toBeUndefined();
  });

  it("raises the backend's message with its status", async () => {
    fetchMock.mockResolvedValue(json({ message: 'Your free plan allows 100 orders. Upgrade for more.' }, 402));
    await expect(api('/orders', { method: 'POST', body: {} })).rejects.toMatchObject({
      status: 402,
      message: 'Your free plan allows 100 orders. Upgrade for more.',
    });
  });

  it('reads the first validation error of a 422', async () => {
    fetchMock.mockResolvedValue(json({ statusCode: 422, detail: [{ loc: ['body', 'quantity'], msg: 'Input should be greater than 0' }] }, 422));
    await expect(api('/orders', { method: 'POST', body: {} })).rejects.toMatchObject({ status: 422, message: 'Input should be greater than 0' });
  });

  it('retries a 401 once with fresh tokens', async () => {
    fetchMock.mockResolvedValueOnce(json({ message: 'Unauthorized' }, 401)).mockResolvedValueOnce(json({ name: 'Acme' }));
    await expect(api('/tenants/me')).resolves.toEqual({ name: 'Acme' });
    expect(session.refreshSession).toHaveBeenCalledOnce();
    expect(session.endSession).not.toHaveBeenCalled();
  });

  it('signs out after a second 401', async () => {
    fetchMock.mockResolvedValue(json({ message: 'Unauthorized' }, 401));
    await expect(api('/tenants/me')).rejects.toMatchObject({ status: 401 });
    expect(session.endSession).toHaveBeenCalledWith('expired');
  });

  it('stops when the session can no longer be refreshed', async () => {
    fetchMock.mockResolvedValue(json({ message: 'Unauthorized' }, 401));
    session.refreshSession.mockResolvedValue(null); // refreshSession signs out itself
    await expect(api('/tenants/me')).rejects.toMatchObject({ status: 401 });
    expect(fetchMock).toHaveBeenCalledOnce();
  });

  it('signs out without calling the API when there is no token', async () => {
    session.getIdToken.mockResolvedValue(null);
    await expect(api('/tenants/me')).rejects.toBeInstanceOf(ApiError);
    expect(fetchMock).not.toHaveBeenCalled();
    expect(session.endSession).toHaveBeenCalledWith('expired');
  });

  it('reports a network failure as status 0', async () => {
    fetchMock.mockRejectedValue(new TypeError('Failed to fetch'));
    await expect(api('/billing')).rejects.toMatchObject({ status: 0 });
  });
});
