import Link from 'next/link';

const links = [['/creatives', 'Creatives'], ['/insights', 'Insights'], ['/fatigue', 'Fatigue'], ['/briefs', 'Briefs'],
  ['/copilot', 'Copilot'], ['/generate', 'Generate'], ['/review', 'Review'], ['/settings', 'Settings']];

export function Nav() {
  return (
    <nav className="flex gap-4 border-b px-6 py-3 text-sm">
      <Link href="/" className="font-semibold">Creative Strategist</Link>
      {links.map(([href, label]) => <Link key={href} href={href} className="text-muted-foreground hover:text-foreground">{label}</Link>)}
    </nav>
  );
}
