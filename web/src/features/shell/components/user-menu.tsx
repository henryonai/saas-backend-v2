import { Link } from '@tanstack/react-router';
import { ChevronsUpDownIcon, LogOutIcon, UserIcon } from 'lucide-react';
import { useTranslation } from 'react-i18next';
import { endSession, useUser } from '@/shared/auth/session';
import { PreferencesMenuItems } from '@/shared/components/preferences';
import { Avatar, AvatarFallback } from '@/shared/components/ui/avatar';
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from '@/shared/components/ui/dropdown-menu';
import { SidebarMenu, SidebarMenuButton, SidebarMenuItem } from '@/shared/components/ui/sidebar';

function initials(email: string): string {
  return email.slice(0, 2).toUpperCase();
}

/** The signed-in user, in the sidebar's footer: account, preferences, sign out. */
export function UserMenu() {
  const { t } = useTranslation();
  const user = useUser();
  return (
    <SidebarMenu>
      <SidebarMenuItem>
        <DropdownMenu>
          <DropdownMenuTrigger asChild>
            <SidebarMenuButton size="lg" className="data-[state=open]:bg-sidebar-accent">
              <Avatar className="size-8 rounded-lg">
                <AvatarFallback className="rounded-lg">{initials(user.email)}</AvatarFallback>
              </Avatar>
              <span className="grid flex-1 text-left text-sm leading-tight">
                <span className="truncate font-medium">{user.email}</span>
                <span className="truncate text-xs text-muted-foreground">{user.role ? t(`roles.${user.role}`) : ''}</span>
              </span>
              <ChevronsUpDownIcon className="ml-auto size-4" />
            </SidebarMenuButton>
          </DropdownMenuTrigger>
          <DropdownMenuContent side="top" align="start" className="w-(--radix-dropdown-menu-trigger-width) min-w-56">
            <DropdownMenuLabel className="truncate font-normal text-muted-foreground">{user.email}</DropdownMenuLabel>
            <DropdownMenuSeparator />
            <DropdownMenuItem asChild>
              <Link to="/account">
                <UserIcon />
                {t('nav.account')}
              </Link>
            </DropdownMenuItem>
            <PreferencesMenuItems />
            <DropdownMenuSeparator />
            <DropdownMenuItem onSelect={() => void endSession()}>
              <LogOutIcon />
              {t('common.signOut')}
            </DropdownMenuItem>
          </DropdownMenuContent>
        </DropdownMenu>
      </SidebarMenuItem>
    </SidebarMenu>
  );
}
