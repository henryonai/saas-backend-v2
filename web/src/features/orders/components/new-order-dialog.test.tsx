import { screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { json, OWNER, renderWithApp } from '@/test/render';
import { NewOrderDialog } from './new-order-dialog';

vi.mock('@/shared/auth/session', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/shared/auth/session')>()),
  getIdToken: vi.fn().mockResolvedValue('id-token'),
}));

const fetchMock = vi.fn<typeof fetch>();

beforeEach(() => {
  vi.stubGlobal('fetch', fetchMock);
  fetchMock.mockReset();
});

async function fillAndSubmit(user: ReturnType<typeof userEvent.setup>) {
  await user.type(screen.getByLabelText('Item'), 'Green tea');
  await user.clear(screen.getByLabelText('Quantity'));
  await user.type(screen.getByLabelText('Quantity'), '3');
  await user.type(screen.getByLabelText('Unit price'), '4.50');
  await user.click(screen.getByRole('button', { name: 'Create order' }));
}

describe('NewOrderDialog', () => {
  it('checks the fields before calling the API', async () => {
    const user = userEvent.setup();
    await renderWithApp(<NewOrderDialog />);
    await user.click(screen.getByRole('button', { name: 'New order' }));
    await user.click(screen.getByRole('button', { name: 'Create order' }));

    expect(screen.getByText('Say what is ordered.')).toBeInTheDocument();
    expect(screen.getByText('An amount like 12 or 12.50.')).toBeInTheDocument();
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it('sends the price in cents', async () => {
    fetchMock.mockResolvedValue(
      json({ order_id: 'o-1', tenant_id: 't', created_by: 'u', item: 'Green tea', quantity: 3, price_cents: 450, status: 'pending', created_at: '' }, 201),
    );
    const user = userEvent.setup();
    await renderWithApp(<NewOrderDialog />);
    await user.click(screen.getByRole('button', { name: 'New order' }));
    expect(screen.getByText('Total:', { exact: false })).toBeInTheDocument();
    await fillAndSubmit(user);

    await waitFor(() => expect(fetchMock).toHaveBeenCalled());
    expect(JSON.parse(fetchMock.mock.calls[0][1]?.body as string)).toEqual({ item: 'Green tea', quantity: 3, price_cents: 450 });
    await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument());
  });

  it('offers the owner an upgrade when the plan is full (402)', async () => {
    fetchMock.mockResolvedValue(json({ message: 'Your free plan allows 100 orders. Upgrade for more.' }, 402));
    const user = userEvent.setup();
    await renderWithApp(<NewOrderDialog />);
    await user.click(screen.getByRole('button', { name: 'New order' }));
    await fillAndSubmit(user);

    expect(await screen.findByText('Your free plan allows 100 orders. Upgrade for more.')).toBeInTheDocument();
    expect(screen.getByRole('link', { name: 'See plans' })).toHaveAttribute('href', '/billing');
  });

  it('tells a member to ask the owner instead', async () => {
    fetchMock.mockResolvedValue(json({ message: 'Your free plan allows 100 orders. Upgrade for more.' }, 402));
    const user = userEvent.setup();
    await renderWithApp(<NewOrderDialog />, { user: { ...OWNER, role: 'member' } });
    await user.click(screen.getByRole('button', { name: 'New order' }));
    await fillAndSubmit(user);

    expect(await screen.findByText('Ask the workspace owner to upgrade the plan.')).toBeInTheDocument();
    expect(screen.queryByRole('link', { name: 'See plans' })).not.toBeInTheDocument();
  });
});
