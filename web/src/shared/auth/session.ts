import { fetchAuthSession, signOut } from 'aws-amplify/auth';
import { Hub } from 'aws-amplify/utils';
import { useStore } from 'zustand';
import { createStore } from 'zustand/vanilla';
import { type SessionUser, userFromClaims } from './claims';

/** Why the app signed the user out, so the sign-in page can say so. */
export type SignedOutReason = 'expired' | 'left';

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
 * workspace, role or plan in the token.
 */
export async function loadSession(forceRefresh = false): Promise<SessionUser | null> {
  const { tokens } = await fetchAuthSession({ forceRefresh });
  const claims = tokens?.idToken?.payload;
  if (!claims) {
    sessionStore.setState({ status: 'signed-out', user: null, reason: null });
    return null;
  }
  const user = userFromClaims(claims);
  sessionStore.setState({ status: 'signed-in', user, reason: null });
  return user;
}

let refreshing: Promise<SessionUser | null> | null = null;

/**
 * New tokens now. Fails when the refresh token was revoked, which the backend
 * does when someone is removed, demoted, or leaves: then the user is signed out.
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
