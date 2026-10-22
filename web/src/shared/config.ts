/**
 * The stack this app talks to: the `ApiUrl`, `UserPoolId` and `UserPoolClientId`
 * outputs of `sam deploy`, read at build time from .env.local
 * (scripts/env-from-stack.sh writes it).
 */
const env = import.meta.env;

export const config = {
  apiUrl: (env.VITE_API_URL ?? '').replace(/\/+$/, ''),
  userPoolId: env.VITE_USER_POOL_ID ?? '',
  userPoolClientId: env.VITE_USER_POOL_CLIENT_ID ?? '',
};

/** The variables still unset, so the app can say which instead of failing on its first call. */
export function missingConfig(): string[] {
  const values: Record<string, string> = {
    VITE_API_URL: config.apiUrl,
    VITE_USER_POOL_ID: config.userPoolId,
    VITE_USER_POOL_CLIENT_ID: config.userPoolClientId,
  };
  return Object.keys(values).filter((name) => !values[name]);
}
