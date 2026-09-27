import { Nav } from '@/components/nav';
import { api } from '@/lib/api';

// Every route under this group is protected (see requireAuth() in each page)
// and gets the app shell -- nav, skip link, main content wrapper. /sign-in
// sits outside this group specifically so it never gets any of this: it's
// the destination for a signed-out user and must be a standalone screen.
export default async function AppLayout({ children }: { children: React.ReactNode }) {
  // The Lab is for admins; a signed-out or failed lookup just hides it.
  const me = await api.me().catch(() => null);
  return (
    <>
      <a
        href="#main"
        className="sr-only focus-visible:not-sr-only focus-visible:fixed focus-visible:top-3 focus-visible:left-3 focus-visible:z-50 focus-visible:rounded-lg focus-visible:bg-primary focus-visible:px-3 focus-visible:py-2 focus-visible:text-sm focus-visible:font-medium focus-visible:text-primary-foreground"
      >
        Skip to content
      </a>
      <div className="flex min-h-dvh flex-col md:flex-row">
        <Nav isAdmin={me?.role === 'admin'} />
        <main id="main" className="flex min-w-0 flex-1 flex-col px-4 py-6 md:px-8 md:py-8">
          {children}
        </main>
      </div>
    </>
  );
}
