'use client';

import Link from 'next/link';
import { usePathname } from 'next/navigation';
import { OrganizationSwitcher, UserButton } from '@clerk/nextjs';
import {
  BatteryWarning,
  FileText,
  LayoutGrid,
  Settings,
  Sparkles,
  TrendingUp,
  type LucideIcon,
} from 'lucide-react';
import { cn } from 'cn';
import { ThemeToggle } from '@/components/theme-toggle';

type NavItem = { href: string; label: string; icon: LucideIcon };

const groups: { label: string; items: NavItem[] }[] = [
  {
    label: 'Workspace',
    items: [
      { href: '/creatives', label: 'Creatives', icon: LayoutGrid },
      { href: '/insights', label: 'Insights', icon: TrendingUp },
      { href: '/fatigue', label: 'Fatigue', icon: BatteryWarning },
      { href: '/briefs', label: 'Briefs', icon: FileText },
    ],
  },
];

const allItems = groups.flatMap((g) => g.items);

function linkClasses(active: boolean) {
  return cn(
    'flex items-center gap-2.5 rounded-lg px-2.5 py-1.5 text-sm font-medium transition-colors',
    active ? 'bg-sidebar-accent text-sidebar-accent-foreground' : 'text-sidebar-foreground/70 hover:bg-sidebar-accent/60 hover:text-sidebar-foreground'
  );
}

export function Nav() {
  const pathname = usePathname();
  const isActive = (href: string) => pathname === href || pathname?.startsWith(`${href}/`);

  return (
    <>
      <aside className="hidden w-56 shrink-0 flex-col gap-6 border-r border-sidebar-border bg-sidebar px-3 py-5 md:flex">
        <Link href="/creatives" className="flex items-center gap-2 px-2 text-sm font-semibold text-sidebar-foreground">
          <Sparkles className="size-4 text-primary" aria-hidden="true" />
          Creative Strategist
        </Link>
        {/* Every page here is org-scoped with no personal-account concept, so
            hidePersonal keeps a user from switching to a context that would 403.
            The switcher's own "Manage Organization" action already opens
            OrganizationProfile (members + invite) as a modal -- no extra page needed. */}
        <OrganizationSwitcher hidePersonal appearance={{ elements: { rootBox: 'w-full', organizationSwitcherTrigger: 'w-full justify-between px-2 py-1.5 rounded-lg hover:bg-sidebar-accent/60' } }} />
        <nav className="flex flex-1 flex-col gap-5" aria-label="Primary">
          {groups.map((group) => (
            <div key={group.label} className="flex flex-col gap-1">
              <span className="px-2.5 text-[11px] font-medium tracking-wide text-sidebar-foreground/50 uppercase">{group.label}</span>
              {group.items.map((item) => (
                <Link key={item.href} href={item.href} aria-current={isActive(item.href) ? 'page' : undefined} className={linkClasses(isActive(item.href))}>
                  <item.icon className="size-4 shrink-0" aria-hidden="true" />
                  {item.label}
                </Link>
              ))}
            </div>
          ))}
        </nav>
        <div className="flex items-center justify-between gap-2 border-t border-sidebar-border pt-3">
          <Link href="/settings" aria-current={isActive('/settings') ? 'page' : undefined} className={cn(linkClasses(isActive('/settings')), 'flex-1')}>
            <Settings className="size-4 shrink-0" aria-hidden="true" />
            Settings
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
