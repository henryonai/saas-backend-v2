import { QueryClient, QueryClientProvider, useQuery } from '@tanstack/react-query';
import { act, render, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import { OWNER } from '@/test/render';
import { sessionStore } from './session';
import { WorkspaceScope } from './workspace-scope';

function WorkspaceName() {
  const tenantId = sessionStore.getState().user?.tenantId;
  const name = useQuery({ queryKey: ['tenant'], queryFn: async () => `name of ${tenantId}` });
  return <p>{name.data ?? 'loading'}</p>;
}

describe('WorkspaceScope', () => {
  it("shows the new workspace's data, not the last one's, on a page that stays open", async () => {
    sessionStore.setState({ status: 'signed-in', user: { ...OWNER, tenantId: 'tenant-1' }, reason: null });
    const queryClient = new QueryClient();
    const unsubscribe = sessionStore.subscribe(() => queryClient.clear()); // as main.tsx does
    render(
      <QueryClientProvider client={queryClient}>
        <WorkspaceScope>
          <WorkspaceName />
        </WorkspaceScope>
      </QueryClientProvider>,
    );
    expect(await screen.findByText('name of tenant-1')).toBeInTheDocument();

    act(() => sessionStore.setState({ user: { ...OWNER, tenantId: 'tenant-2' } }));
    expect(await screen.findByText('name of tenant-2')).toBeInTheDocument();
    expect(screen.queryByText('name of tenant-1')).not.toBeInTheDocument();
    unsubscribe();
  });
});
