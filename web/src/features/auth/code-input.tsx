import { REGEXP_ONLY_DIGITS } from 'input-otp';
import { InputOTP, InputOTPGroup, InputOTPSlot } from '@/shared/components/ui/input-otp';

/** The 6-digit codes Cognito emails (sign-up, password reset) and authenticator apps show. */
export function CodeInput({ id, value, onChange, onComplete }: {
  id?: string;
  value: string;
  onChange: (value: string) => void;
  onComplete?: (value: string) => void;
}) {
  return (
    <InputOTP id={id} maxLength={6} pattern={REGEXP_ONLY_DIGITS} value={value} onChange={onChange} onComplete={onComplete} autoFocus>
      <InputOTPGroup>
        {Array.from({ length: 6 }, (_, index) => (
          <InputOTPSlot key={index} index={index} />
        ))}
      </InputOTPGroup>
    </InputOTP>
  );
}
