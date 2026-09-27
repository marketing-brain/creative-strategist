import { FileCard } from '@/components/account-read';
import { Mark } from '@/components/account-read-evidence';
import type { ProfileRead as Read, ProfileReadVideo } from '@/lib/types';
import type { Formatters } from '@/lib/format';

// What separates a cluster's best videos from its worst: insights checked against the data
// (numbers computed in code, visual facts verified on frames). The model's own read stays in
// the stored result for auditing and is not shown.

// A lead is shown on purpose: it did not clear the bar (at least 3 scripts on each side and
// p < 0.1), and the badge says so.
const BADGE: Record<'holds' | 'context' | 'lead', { label: string; cls: string }> = {
  holds: {
    label: 'Checked against the data',
    cls: 'bg-emerald-100 text-emerald-900 dark:bg-emerald-950 dark:text-emerald-200',
  },
  lead: { label: 'Lead · not proven', cls: 'bg-amber-100 text-amber-900 dark:bg-amber-950 dark:text-amber-200' },
  context: { label: 'Context', cls: 'bg-muted text-muted-foreground' },
};

const SIDE: Record<ProfileReadVideo['side'], string> = { best: 'Best', middle: 'Middle', worst: 'Worst', unrankable: 'Thin' };

function unit(v: ProfileReadVideo) {
  return {
    sha256: v.code,
    headline: v.name,
    spend: v.spend,
    creative_ids: v.rep_creative_id ? [v.rep_creative_id] : [],
    rep_creative_id: v.rep_creative_id,
    duration_s: null,
    impressions: 0,
    purchases: v.cpa ? v.spend / v.cpa : 0,
    poster_url: v.poster_url,
  };
}

function Ads({ codes, videos, f }: { codes: string[]; videos: Record<string, ProfileReadVideo>; f: Formatters }) {
  const shown = codes.filter((c) => videos[c]).slice(0, 3);
  if (!shown.length) return null;
  return (
    <div className="md:w-[22rem]">
      <Mark className="text-muted-foreground">Watch these</Mark>
      <div className="mt-2 grid grid-cols-3 gap-3">
        {shown.map((c) => (
          <FileCard key={c} name={videos[c].name} unit={unit(videos[c])} f={f} note={`${c} · ${SIDE[videos[c].side]}`} />
        ))}
      </div>
    </div>
  );
}

export function ProfileRead({ data, f }: { data: Read; f: Formatters }) {
  if (data.status !== 'done' || !data.videos) return null;
  const videos = data.videos;
  const reviewed = data.reviewed ?? [];
  return (
    <div className="space-y-2">
      {reviewed.map((r, i) => (
        <article key={i} className="grid gap-x-8 gap-y-3 border-t border-border py-8 first:border-t-0 first:pt-2 md:grid-cols-[minmax(0,1fr)_auto]">
          <div className="min-w-0 max-w-3xl space-y-4">
            <div className="flex flex-wrap items-center gap-2">
              <span className={`rounded-sm px-1.5 py-0.5 text-[11px] font-semibold uppercase tracking-[0.06em] ${BADGE[r.strength].cls}`}>
                {BADGE[r.strength].label}
              </span>
              <span className="font-mono text-[12px] tabular-nums text-muted-foreground">
                {r.p != null ? (r.p < 0.001 ? 'p < 0.001' : `p = ${r.p.toFixed(3)}`) : 'no test'}
              </span>
            </div>
            <h4 className="max-w-[34ch] text-lg font-semibold leading-snug tracking-tight text-balance">{r.title}</h4>
            <dl className="grid max-w-3xl grid-cols-2 gap-x-6 gap-y-2 sm:grid-cols-4">
              {r.numbers.map((n) => (
                <div key={n.label} className="min-w-0">
                  <dt className="text-[12px] text-muted-foreground">{n.label}</dt>
                  <dd className="font-mono text-[13px] tabular-nums">{n.value}</dd>
                </div>
              ))}
            </dl>
            <p className="max-w-[68ch] border-l-2 border-border pl-4 text-[15px] leading-7 text-pretty">{r.body}</p>
            <p className="text-[12px] text-muted-foreground">{r.basis}</p>
          </div>
          <Ads codes={r.videos} videos={videos} f={f} />
        </article>
      ))}

    </div>
  );
}
