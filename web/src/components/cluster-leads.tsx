import { FileCard } from '@/components/account-read';
import { Mark } from '@/components/account-read-evidence';
import type { ClusterLeads as Leads, ClusterLeadVideo } from '@/lib/types';
import type { Formatters } from '@/lib/format';

// Leads from a model watching the cluster's 3 best and 3 worst videos side by side. Laid out
// like the Insights tab's findings, but never graded: every point names timestamps, and the
// ads sit beside it so the reader checks by watching.

function unit(v: ClusterLeadVideo) {
  return {
    sha256: v.label,
    headline: v.name,
    spend: v.spend ?? 0,
    creative_ids: v.rep_creative_id ? [v.rep_creative_id] : [],
    rep_creative_id: v.rep_creative_id,
    duration_s: null,
    impressions: 0,
    // FileCard shows cost per purchase as spend / purchases.
    purchases: v.spend && v.cpa ? v.spend / v.cpa : 0,
    poster_url: v.poster_url,
  };
}

export function ClusterLeads({ data, f }: { data: Leads; f: Formatters }) {
  if (data.status !== 'done' || !data.answer || !data.videos) return null;
  const byLabel = new Map(data.videos.map((v) => [v.label, v]));
  const { summary, differences, per_video, caveats } = data.answer;
  const read = new Map(per_video.map((p) => [p.video, p.read]));
  return (
    <div className="space-y-2">
      <div className="max-w-[68ch] space-y-2">
        <p className="text-[15px] leading-7 text-pretty">{summary}</p>
      </div>

      {differences.map((d, i) => {
        const shown = [...d.have_it, ...d.lack_it].filter((l) => byLabel.has(l)).slice(0, 3);
        return (
          <article key={i} className="grid gap-x-8 gap-y-3 border-t border-border py-8 md:grid-cols-[minmax(0,1fr)_auto]">
            <div className="min-w-0 max-w-3xl space-y-4">
              <h4 className="max-w-[34ch] text-lg font-semibold leading-snug tracking-tight text-balance">{d.point}</h4>
              <p className="text-[13px] text-muted-foreground">
                Favours the {d.favours}. Has it: {d.have_it.join(', ') || 'none'}. Lacks it: {d.lack_it.join(', ') || 'none'}.
              </p>
              <ul className="max-w-[68ch] space-y-2 border-l-2 border-border pl-4 text-[15px] leading-7">
                {d.evidence.map((e, j) => (
                  <li key={j}>
                    <span className="mr-2 font-mono text-[13px] tabular-nums text-muted-foreground">
                      {e.video} · {e.at}
                    </span>
                    {e.what}
                  </li>
                ))}
              </ul>
            </div>
            {shown.length > 0 && (
              <div className="md:w-[22rem]">
                <Mark className="text-muted-foreground">Watch these</Mark>
                <div className="mt-2 grid grid-cols-3 gap-3">
                  {shown.map((l) => {
                    const v = byLabel.get(l)!;
                    return <FileCard key={l} name={v.name} unit={unit(v)} f={f} note={l} />;
                  })}
                </div>
              </div>
            )}
          </article>
        );
      })}

      <details className="group/all border-t border-border pt-4">
        <summary className="cursor-pointer list-none text-[13px] font-medium text-muted-foreground hover:text-foreground [&::-webkit-details-marker]:hidden">
          All six videos, and what to keep in mind
        </summary>
        <div className="mt-4 grid grid-cols-3 gap-3 sm:grid-cols-6">
          {data.videos.map((v) => (
            <FileCard key={v.label} name={v.name} unit={unit(v)} f={f} note={v.label} />
          ))}
        </div>
        <ul className="mt-4 max-w-[68ch] space-y-2 text-[14px] leading-6">
          {data.videos.map((v) => (
            <li key={v.label}>
              <span className="mr-2 font-mono text-[13px] text-muted-foreground">{v.label}</span>
              {read.get(v.label)}
            </li>
          ))}
        </ul>
        {caveats.length > 0 && (
          <ul className="mt-4 max-w-[68ch] list-disc space-y-1 pl-5 text-[14px] leading-6 text-muted-foreground">
            {caveats.map((c, i) => (
              <li key={i}>{c}</li>
            ))}
          </ul>
        )}
      </details>
    </div>
  );
}
