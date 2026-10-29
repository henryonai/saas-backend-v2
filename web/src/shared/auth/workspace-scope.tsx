import { Fragment, type ReactNode } from 'react';
import { useStore } from 'zustand';
import { sessionStore } from './session';

/**
 * Renders its children again from scratch when the tab moves to another
 * workspace or user. main.tsx clears the query cache then, but clearing it
 * doesn't refetch a page that stays on screen: without this, it would keep
 * showing the previous workspace's data until it was opened again.
 */
export function WorkspaceScope({ children }: { children: ReactNode }) {
  const scope = useStore(sessionStore, (state) => `${state.user?.userId ?? ''}/${state.user?.tenantId ?? ''}`);
  return <Fragment key={scope}>{children}</Fragment>;
}
