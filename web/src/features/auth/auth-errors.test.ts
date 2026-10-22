import { describe, expect, it } from 'vitest';
import { i18n } from '@/shared/i18n';
import { authErrorMessage } from './auth-errors';

const named = (name: string, message = 'from Cognito') => Object.assign(new Error(message), { name });

describe('authErrorMessage', () => {
  const t = i18n.getFixedT('en');

  it('translates the Cognito errors a user can fix', () => {
    expect(authErrorMessage(named('NotAuthorizedException'), t)).toBe('Incorrect email or password.');
    expect(authErrorMessage(named('CodeMismatchException'), t)).toBe("That code isn't right. Check it and try again.");
    expect(authErrorMessage(named('LimitExceededException'), t)).toMatch(/Too many attempts/);
  });

  it("falls back to Cognito's own message", () => {
    expect(authErrorMessage(named('InvalidParameterException', 'Invalid email address format.'), t)).toBe('Invalid email address format.');
    expect(authErrorMessage('nope', t)).toBe('Something went wrong. Please try again.');
  });
});
