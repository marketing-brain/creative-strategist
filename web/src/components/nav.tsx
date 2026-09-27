'use client';

import { useEffect, useState } from 'react';
import Link from 'next/link';
import { usePathname } from 'next/navigation';
import { OrganizationSwitcher, UserButton } from '@clerk/nextjs';
import {
  Clapperboard,
  FlaskConical,
  LayoutGrid,
  Microscope,
  Globe,
  MessageCircleQuestion,
  PanelLeftClose,
  PanelLeftOpen,
  Settings,
  Sparkles,
  TrendingUp,
  type LucideIcon,
} from 'lucide-react';
import { cn } from 'cn';
import { Button } from '@/components/ui/button';
import { ThemeToggle } from '@/components/theme-toggle';

type NavItem = { href: string; label: string; icon: LucideIcon; also?: string[] };

// The site follows the loop an in-house marketer runs (2026-09-26 rework): look at the ads,
// understand what works, make new versions, test them.
// Each page ends by pointing at the next one. Research that explains how the analysis works
// lives in the Lab, visible to admins only.
const journey: NavItem[] = [
  { href: '/creatives', label: 'Ads', icon: LayoutGrid, also: ['/campaigns', '/clusters'] },
  { href: '/what-works', label: 'What works', icon: TrendingUp },
  { href: '/make', label: 'Make', icon: Clapperboard, also: ['/new-video', '/briefs', '/generate'] },
  { href: '/tests', label: 'Tests', icon: FlaskConical },
];
const lab: NavItem[] = [
  // Account-wide reads compare across kinds of video, which confuses format with content.
  // Kept for reference only; What works shows findings from inside a cluster.
  { href: '/insights', label: 'Account-wide read', icon: Globe },
  { href: '/ask', label: 'Ask', icon: MessageCircleQuestion },
  { href: '/experiments', label: 'Research', icon: Microscope },
];

function linkClasses(active: boolean) {
  return cn(
    'flex items-center gap-2.5 rounded-lg px-2.5 py-1.5 text-sm font-medium transition-colors rail:justify-center rail:px-0',
    active ? 'bg-sidebar-accent text-sidebar-accent-foreground' : 'text-sidebar-foreground/70 hover:bg-sidebar-accent/60 hover:text-sidebar-foreground'
  );
}

export function Nav({ isAdmin = false }: { isAdmin?: boolean }) {
  const pathname = usePathname();
  const under = (href: string) => pathname === href || !!pathname?.startsWith(`${href}/`);
  const isActive = (href: string) => {
    const item = [...journey, ...lab].find((i) => i.href === href);
    return under(href) || !!item?.also?.some(under);
  };
  const groups = [
    { label: null as string | null, items: journey },
    ...(isAdmin ? [{ label: 'Lab', items: lab }] : []),
  ];
  const allItems = groups.flatMap((g) => g.items);

  // The rail's *appearance* comes entirely from the `sidebar-collapsed` class the init
  // script puts on <html>, so it is correct on the first paint. This state only drives
  // the toggle's own label/icon and the tooltips, which have nothing to flash.
  const [collapsed, setCollapsed] = useState(false);
  useEffect(() => {
    setCollapsed(document.documentElement.classList.contains('sidebar-collapsed'));
  }, []);

  const toggleSidebar = () => {
    const next = !document.documentElement.classList.contains('sidebar-collapsed');
    document.documentElement.classList.toggle('sidebar-collapsed', next);
    localStorage.setItem('sidebar', next ? 'collapsed' : 'expanded');
    setCollapsed(next);
  };

  const tip = (label: string) => (collapsed ? label : undefined);

  return (
    <>
      <aside className="hidden w-60 shrink-0 flex-col gap-6 border-r border-sidebar-border bg-sidebar px-3 py-5 transition-[width] duration-200 md:flex rail:w-14 rail:px-2">
        <div className="flex items-center gap-2 px-2 rail:flex-col rail:gap-3 rail:px-0">
          <Link
            href="/creatives"
            title={tip('Creative Strategist')}
            className="flex min-w-0 items-center gap-2 text-sm font-semibold text-sidebar-foreground"
          >
            <Sparkles className="size-4 shrink-0 text-primary" aria-hidden="true" />
            <span className="truncate rail:sr-only">Creative Strategist</span>
          </Link>
          <Button
            variant="ghost"
            size="icon-sm"
            onClick={toggleSidebar}
            aria-label={collapsed ? 'Expand sidebar' : 'Collapse sidebar'}
            aria-expanded={!collapsed}
            className="ml-auto shrink-0 rail:ml-0"
          >
            {collapsed ? <PanelLeftOpen className="size-4" aria-hidden="true" /> : <PanelLeftClose className="size-4" aria-hidden="true" />}
          </Button>
        </div>
        {/* Every page here is org-scoped with no personal-account concept, so
            hidePersonal keeps a user from switching to a context that would 403.
            The switcher's own "Manage Organization" action already opens
            OrganizationProfile (members + invite) as a modal -- no extra page needed. */}
        <div className="rail:hidden">
          <OrganizationSwitcher hidePersonal appearance={{ elements: { rootBox: 'w-full', organizationSwitcherTrigger: 'w-full justify-between px-2 py-1.5 rounded-lg hover:bg-sidebar-accent/60' } }} />
        </div>
        <nav className="flex flex-1 flex-col gap-5" aria-label="Primary">
          {groups.map((group) => (
            <div key={group.label ?? "main"} className="flex flex-col gap-1">
              {group.label && (
                <span className="px-2.5 pt-2 text-sm font-medium text-sidebar-foreground/60 rail:hidden">{group.label}</span>
              )}
              {group.items.map((item) => (
                <Link
                  key={item.href}
                  href={item.href}
                  title={tip(item.label)}
                  aria-current={isActive(item.href) ? 'page' : undefined}
                  className={linkClasses(isActive(item.href))}
                >
                  <item.icon className="size-4 shrink-0" aria-hidden="true" />
                  <span className="truncate rail:sr-only">{item.label}</span>
                </Link>
              ))}
            </div>
          ))}
        </nav>
        <div className="flex items-center justify-between gap-2 border-t border-sidebar-border pt-3 rail:flex-col rail:gap-3">
          <Link
            href="/settings"
            title={tip('Settings')}
            aria-current={isActive('/settings') ? 'page' : undefined}
            className={cn(linkClasses(isActive('/settings')), 'flex-1 rail:w-full rail:flex-none')}
          >
            <Settings className="size-4 shrink-0" aria-hidden="true" />
            <span className="truncate rail:sr-only">Settings</span>
          </Link>
          <UserButton />
          <ThemeToggle />
        </div>
      </aside>

      <nav className="flex items-center gap-1 overflow-x-auto border-b border-border px-3 py-2 md:hidden" aria-label="Primary">
        {allItems.map((item) => (
          <Link
            key={item.href}
            href={item.href}
            aria-current={isActive(item.href) ? 'page' : undefined}
            className={cn(
              'flex shrink-0 items-center gap-1.5 rounded-lg px-2.5 py-1.5 text-sm font-medium',
              isActive(item.href) ? 'bg-accent text-accent-foreground' : 'text-muted-foreground'
            )}
          >
            <item.icon className="size-4" aria-hidden="true" />
            {item.label}
          </Link>
        ))}
        <OrganizationSwitcher hidePersonal />
        <Link
          href="/settings"
          aria-label="Settings"
          aria-current={isActive('/settings') ? 'page' : undefined}
          className={cn(
            'ml-auto flex shrink-0 items-center rounded-lg p-1.5',
            isActive('/settings') ? 'bg-accent text-accent-foreground' : 'text-muted-foreground'
          )}
        >
          <Settings className="size-4" aria-hidden="true" />
        </Link>
        <UserButton />
      </nav>
    </>
  );
}
