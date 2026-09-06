import { ReviewCard } from '@/components/review-card';
import { api } from '@/lib/api';

export default async function Review({ searchParams }: { searchParams: Promise<{ state?: string }> }) {
  const { state = 'draft' } = await searchParams;
  const [assets, accounts, jobs, me] = await Promise.all([api.reviewQueue(state), api.accounts(), api.publishJobs(), api.me()]);
  const isAdmin = me.role === 'admin';
  return (
    <div className="space-y-6">
      <div className="flex items-center gap-4"><h1 className="text-xl font-semibold">Review queue</h1>
        {['draft', 'approved', 'rejected'].map((s) => <a key={s} href={`/review?state=${s}`} className={`text-sm ${s === state ? 'font-semibold underline' : 'text-muted-foreground'}`}>{s}</a>)}</div>
      <div className="grid gap-4 md:grid-cols-3 lg:grid-cols-4">{assets.map((a) => <ReviewCard key={a.id} asset={a} accounts={accounts.filter((x) => x.status === 'active')} isAdmin={isAdmin} />)}</div>
      <section><h2 className="mb-2 font-semibold">Publish jobs</h2><ul className="divide-y rounded border text-sm">{jobs.map((j) => <li key={j.id} className="flex justify-between p-2"><span>{j.generated_asset_id}</span><span>{j.status}{j.platform_ad_id ? ` · ad ${j.platform_ad_id}` : ''}{j.error ? ` — ${j.error}` : ''}</span></li>)}</ul></section>
    </div>
  );
}
