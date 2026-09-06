import './globals.css';
import { ClerkProvider } from '@clerk/nextjs';
import { Nav } from '@/components/nav';

export const metadata = { title: 'Creative Strategist' };

export default function RootLayout({ children }: { children: React.ReactNode }) {
  const body = (
    <html lang="en"><body className="min-h-screen bg-background text-foreground"><Nav /><main className="p-6">{children}</main></body></html>
  );
  return process.env.NEXT_PUBLIC_AUTH_MODE === 'clerk' ? <ClerkProvider>{body}</ClerkProvider> : body;
}
