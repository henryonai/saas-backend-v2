/** A path inside this app to go to after sign-in, or "/" for anything else (another origin, junk). */
export function safeRedirect(target: string | undefined | null): string {
  if (!target || !target.startsWith('/') || target.startsWith('//') || target.startsWith('/\\')) return '/';
  return target;
}
