import { screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { endSession } from '@/shared/auth/session';
import { json, renderWithApp } from '@/test/render';
import { DeleteAccount } from './components/delete-account';

vi.mock('@/shared/auth/session', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/shared/auth/session')>();
  return { ...actual, getIdToken: vi.fn().mockResolvedValue('id-token'), endSession: vi.fn().mockResolvedValue(undefined) };
});

const fetchMock = vi.fn<typeof fetch>();
let answer: Response;

beforeEach(() => {
  vi.mocked(endSession).mockClear();
  answer = new Response(null, { status: 204 });
  vi.stubGlobal('fetch', fetchMock);
  fetchMock.mockReset().mockImplementation(async () => answer);
});

async function confirm() {
  const user = userEvent.setup();
  await renderWithApp(<DeleteAccount />);
  await user.click(screen.getByRole('button', { name: 'Delete account' }));
  const submit = screen.getByRole('button', { name: 'Delete my account' });
  expect(submit).toBeDisabled();
  await user.type(screen.getByLabelText('Type owner@example.com to confirm'), 'Owner@Example.com');
  await user.click(submit);
}

describe('DeleteAccount', () => {
  it('deletes the account once the email is typed, then signs out', async () => {
    await confirm();
    await waitFor(() => expect(endSession).toHaveBeenCalled());
    const [input, init] = fetchMock.mock.calls[0];
    expect(String(input)).toMatch(/\/account$/);
    expect(init?.method).toBe('DELETE');
    expect(JSON.parse(init?.body as string)).toEqual({ email: 'Owner@Example.com' });
  });

  it("shows why it was refused, and stays signed in", async () => {
    answer = json({ message: 'Delete the workspaces you own first: Acme Coffee' }, 409);
    await confirm();
    expect(await screen.findByText(/Acme Coffee/)).toBeInTheDocument();
    expect(endSession).not.toHaveBeenCalled();
  });
});
