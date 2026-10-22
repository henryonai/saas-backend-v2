import type { TFunction } from 'i18next';

/** Cognito's errors (thrown by Amplify with the service's exception name), in the user's language. */
export function authErrorMessage(error: unknown, t: TFunction): string {
  const name = error instanceof Error ? error.name : '';
  switch (name) {
    case 'NotAuthorizedException':
      return t('auth.errors.wrongPassword');
    case 'UsernameExistsException':
      return t('auth.errors.emailTaken');
    case 'InvalidPasswordException':
      return t('auth.errors.invalidPassword');
    case 'CodeMismatchException':
      return t('auth.errors.wrongCode');
    case 'ExpiredCodeException':
      return t('auth.errors.expiredCode');
    case 'LimitExceededException':
    case 'TooManyRequestsException':
    case 'TooManyFailedAttemptsException':
      return t('auth.errors.tooManyAttempts');
    case 'NetworkError':
      return t('errors.network');
  }
  return error instanceof Error && error.message ? error.message : t('errors.generic');
}
