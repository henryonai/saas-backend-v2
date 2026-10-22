import { Amplify } from 'aws-amplify';
import { config } from '../config';

/**
 * Amplify is used for Cognito only: sign-up, sign-in (SRP, the one flow the app
 * client allows), and keeping and refreshing the tokens in localStorage.
 */
export function configureAmplify(): void {
  Amplify.configure({
    Auth: {
      Cognito: {
        userPoolId: config.userPoolId,
        userPoolClientId: config.userPoolClientId,
        loginWith: { email: true },
        signUpVerificationMethod: 'code',
      },
    },
  });
}
