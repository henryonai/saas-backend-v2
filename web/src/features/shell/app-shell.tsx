import { Link, Outlet, useMatchRoute } from '@tanstack/react-router';
import { BellIcon, BotIcon, CreditCardIcon, LayoutDashboardIcon, PackageIcon, UsersIcon } from 'lucide-react';
import { useTranslation } from 'react-i18next';
import { Badge } from '@/shared/components/ui/badge';
import {
  Sidebar,
  SidebarContent,
  SidebarFooter,
  SidebarGroup,
  SidebarGroupContent,
  SidebarHeader,
  SidebarInset,
  SidebarMenu,
  SidebarMenuBadge,
  SidebarMenuButton,
  SidebarMenuItem,
  SidebarProvider,
  SidebarRail,
  SidebarTrigger,
} from '@/shared/components/ui/sidebar';
import { useNotifications } from '../notifications/hooks';
import { NotificationBell } from '../notifications/components/notification-bell';
import { WorkspaceSwitcher } from '../workspace/workspace-switcher';
import { UserMenu } from './components/user-menu';

const NAV = [
  { to: '/', label: 'nav.overview', icon: LayoutDashboardIcon, exact: true },
  { to: '/orders', label: 'nav.orders', icon: PackageIcon, exact: false },
  { to: '/assistant', label: 'nav.assistant', icon: BotIcon, exact: false },
  { to: '/team', label: 'nav.team', icon: UsersIcon, exact: false },
  { to: '/billing', label: 'nav.billing', icon: CreditCardIcon, exact: false },
  { to: '/notifications', label: 'nav.notifications', icon: BellIcon, exact: false },
] as const;

export function AppShell() {
  const { t } = useTranslation();
  return (
    <SidebarProvider>
      <AppSidebar />
      <SidebarInset>
        <header className="sticky top-0 z-10 flex h-14 shrink-0 items-center gap-2 border-b bg-background/95 px-4 backdrop-blur">
          <SidebarTrigger className="-ml-1" aria-label={t('nav.toggleSidebar')} />
          <div className="ml-auto flex items-center gap-1">
            <NotificationBell />
          </div>
        </header>
        <main className="flex-1 p-4 md:p-8">
          <div className="mx-auto w-full max-w-6xl">
            <Outlet />
          </div>
        </main>
      </SidebarInset>
    </SidebarProvider>
  );
}

function AppSidebar() {
  const { t } = useTranslation();
  const matchRoute = useMatchRoute();
  // shares the bell's query (and its polling)
  const unread = (useNotifications().data ?? []).filter((item) => !item.read).length;

  return (
    <Sidebar collapsible="icon">
      <SidebarHeader>
        <WorkspaceSwitcher />
      </SidebarHeader>
      <SidebarContent>
        <SidebarGroup>
          <SidebarGroupContent>
            <SidebarMenu>
              {NAV.map((item) => (
                <SidebarMenuItem key={item.to}>
                  <SidebarMenuButton asChild isActive={!!matchRoute({ to: item.to, fuzzy: !item.exact })} tooltip={t(item.label)}>
                    <Link to={item.to}>
                      <item.icon />
                      <span>{t(item.label)}</span>
                    </Link>
                  </SidebarMenuButton>
                  {item.to === '/notifications' && unread > 0 && (
                    <SidebarMenuBadge>
                      <Badge className="h-5 min-w-5 px-1">{unread > 9 ? '9+' : unread}</Badge>
                    </SidebarMenuBadge>
                  )}
                </SidebarMenuItem>
              ))}
            </SidebarMenu>
          </SidebarGroupContent>
        </SidebarGroup>
      </SidebarContent>
      <SidebarFooter>
        <UserMenu />
      </SidebarFooter>
      <SidebarRail />
    </Sidebar>
  );
}
