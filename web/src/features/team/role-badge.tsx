import { useTranslation } from 'react-i18next';
import type { Role } from '@/shared/auth/claims';
import { Badge } from '@/shared/components/ui/badge';

export function RoleBadge({ role }: { role: Role }) {
  const { t } = useTranslation();
  return <Badge variant={role === 'owner' ? 'default' : role === 'admin' ? 'secondary' : 'outline'}>{t(`roles.${role}`)}</Badge>;
}
