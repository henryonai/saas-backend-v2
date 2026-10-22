import { type ParsedLocation, redirect } from '@tanstack/react-router';
import { sessionStore } from './session';

/** For the sign-in and sign-up pages. */
export function requireSignedOut(): void {
  if (sessionStore.getState().status === 'signed-in') throw redirect({ to: '/' });
}

function requireSignedIn(location: ParsedLocation) {
  const { user } = sessionStore.getState();
  if (!user) throw redirect({ to: '/sign-in', search: { redirect: location.href } });
  return user;
}

/** For the app itself: signed in, with a workspace. */
export function requireWorkspace(location: ParsedLocation): void {
  if (!requireSignedIn(location).tenantId) throw redirect({ to: '/welcome' });
}

/** For creating or joining a workspace: signed in, without one. */
export function requireNoWorkspace(location: ParsedLocation): void {
  if (requireSignedIn(location).tenantId) throw redirect({ to: '/' });
}
