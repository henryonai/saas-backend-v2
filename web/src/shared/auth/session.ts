import { fetchAuthSession, signOut } from 'aws-amplify/auth';
import { Hub } from 'aws-amplify/utils';
import { useStore } from 'zustand';
import { createStore } from 'zustand/vanilla';
import { inWorkspace, type SessionUser, userFromClaims } from './claims';

/** Why the app signed the user out, so the sign-in page can say so. */
export type SignedOutReason = 'expired';

// The workspace this tab works in (sessionStorage is per tab), and where a new tab starts.
const TAB_WORKSPACE = 'saas.workspace';
const LAST_WORKSPACE = 'saas.lastWorkspace';

function chosenWorkspace(): string | null {
  try {
    return sessionStorage.getItem(TAB_WORKSPACE) ?? localStorage.getItem(LAST_WORKSPACE);
  } catch {
    return null; // storage blocked: the token's first workspace
  }
}

function rememberWorkspace(tenantId: string | null): void {
  if (!tenantId) return;
  try {
    sessionStorage.setItem(TAB_WORKSPACE, tenantId);
    localStorage.setItem(LAST_WORKSPACE, tenantId);
  } catch {
    // storage blocked: this tab still works in it until reloaded
  }
}

export type SessionState =
  | { status: 'signed-out'; user: null; reason: SignedOutReason | null }
  | { status: 'signed-in'; user: SessionUser; reason: null };

export const sessionStore = createStore<SessionState>()(() => ({ status: 'signed-out', user: null, reason: null }));

export function useSession(): SessionState {
  return useStore(sessionStore);
}

/** The signed-in user, for components rendered inside the signed-in layouts only. */
export function useUser(): SessionUser {
  const user = useStore(sessionStore, (state) => state.user);
  if (!user) throw new Error('useUser() needs a signed-in user');
  return user;
}

/**
 * Reads the tokens Amplify keeps. `forceRefresh` gets new ones from Cognito,
 * which runs the login trigger again: use it after anything that changes the
 * user's workspaces, roles or plans. The tab keeps working in its workspace
 * while the token lists it.
 */
export async function loadSession(forceRefresh = false): Promise<SessionUser | null> {
  const { tokens } = await fetchAuthSession({ forceRefresh });
  const claims = tokens?.idToken?.payload;
  if (!claims) {
    sessionStore.setState({ status: 'signed-out', user: null, reason: null });
    return null;
  }
  const user = userFromClaims(claims, chosenWorkspace());
  rememberWorkspace(user.tenantId);
  sessionStore.setState({ status: 'signed-in', user, reason: null });
  return user;
}

/**
 * Works in another of the user's workspaces, in this tab only (other tabs keep
 * theirs). False if the token doesn't list it: refresh the session first.
 */
export function switchWorkspace(tenantId: string): boolean {
  const { user } = sessionStore.getState();
  if (!user?.workspaces.some((w) => w.tenantId === tenantId)) return false;
  rememberWorkspace(tenantId);
  if (user.tenantId !== tenantId) sessionStore.setState({ status: 'signed-in', user: inWorkspace(user, tenantId), reason: null });
  return true;
}

let refreshing: Promise<SessionUser | null> | null = null;

/**
 * New tokens now. Fails when the refresh token was revoked, which the backend
 * does when someone is removed or their role changes: then the user is signed out.
 * Calls made at the same time share one refresh.
 */
export function refreshSession(): Promise<SessionUser | null> {
  refreshing ??= (async () => {
    try {
      const user = await loadSession(true);
      if (user) return user;
    } catch {
      // refresh token expired or revoked
    }
    await endSession('expired');
    return null;
  })().finally(() => {
    refreshing = null;
  });
  return refreshing;
}

/** The ID token for the API (it carries the workspace claims; access tokens are rejected). */
export async function getIdToken(): Promise<string | null> {
  const { tokens } = await fetchAuthSession();
  return tokens?.idToken?.toString() ?? null;
}

export async function endSession(reason: SignedOutReason | null = null): Promise<void> {
  try {
    await signOut();
  } catch {
    // the tokens may be revoked already; Amplify still forgets them locally
  }
  sessionStore.setState({ status: 'signed-out', user: null, reason });
}

/** Keeps the store in step with Amplify's own refreshes (every 15 minutes, with the ID token). */
export function listenToAuthEvents(): () => void {
  return Hub.listen('auth', ({ payload }) => {
    if (payload.event === 'tokenRefresh') void loadSession().catch(() => {});
    if (payload.event === 'tokenRefresh_failure') void endSession('expired');
  });
}
