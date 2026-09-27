'use client';

import { useState } from 'react';
import Link from 'next/link';
import { useRouter } from 'next/navigation';
import { ArrowUpRight, Check, Copy, FileText, Loader2, MoreHorizontal, Pencil, Plus, Trash2, Upload, X } from 'lucide-react';
import { cn } from 'cn';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Textarea } from '@/components/ui/textarea';
import { api, ApiError } from '@/lib/api';
import type { BriefContent, BriefVariant, FocusFinding, MakeVideo } from '@/lib/types';
import { BatchBar, Ideas } from './batch';
import { FocusCard, PLAYER_ID, Timeline, VerdictPanel } from './video-top';
import {
  GroupHeader, ReasonChips, clock, clock1, parseClock, shortTitle,
} from './shared';

const fmtCuts = (segs: [number, number][]) => segs.map(([a, b]) => `${clock1(a)} to ${clock1(b)}`).join(', ');
const parseCuts = (v: string): [number, number][] | null => {
  const out: [number, number][] = [];
  for (const part of v.split(',').map((x) => x.trim()).filter(Boolean)) {
    const m = part.split(/\s*(?:to|[–-])\s*/);
    if (m.length !== 2) return null;
    const a = parseClock(m[0]);
    const b = parseClock(m[1]);
    if (!(b > a)) return null;
    out.push([a, b]);
  }
  return out.length ? out : null;
};

function StringList({ items, editing, onChange, ordered, label }: {
  items: string[];
  editing: boolean;
  onChange: (v: string[]) => void;
  ordered?: boolean;
  label: string;
}) {
  if (!editing) {
    const Tag = ordered ? 'ol' : 'ul';
    return (
      <Tag className={cn('max-w-prose space-y-2 pl-5 text-sm', ordered ? 'list-decimal' : 'list-disc')}>
        {items.map((s, i) => <li key={i}>{s}</li>)}
      </Tag>
    );
  }
  return (
    <div className="space-y-2">
      {items.map((s, i) => (
        <div key={i} className="flex items-start gap-2">
          <Textarea value={s} aria-label={`${label} ${i + 1}`} onChange={(e) => onChange(items.map((x, j) => (j === i ? e.target.value : x)))} />
          <Button variant="ghost" size="icon" aria-label={`Remove ${label.toLowerCase()}`} onClick={() => onChange(items.filter((_, j) => j !== i))}><Trash2 /></Button>
        </div>
      ))}
      <Button variant="outline" size="sm" onClick={() => onChange([...items, ''])}><Plus /> Add</Button>
    </div>
  );
}

function NoBrief({ v, finding }: { v: MakeVideo; finding: FocusFinding | null }) {
  const router = useRouter();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  return (
    <div className="flex flex-col items-start gap-3 rounded-xl border border-dashed border-border p-6">
      <FileText className="size-6 text-muted-foreground" aria-hidden="true" />
      <div className="space-y-1">
        <p className="text-base font-semibold">{finding ? 'Write the brief around the finding' : 'No brief for this video yet'}</p>
        <p className="max-w-prose text-sm text-muted-foreground">
          A brief maps the video line by line, then proposes re-edits of this file, new opening lines to shoot and a script for a new creator. One model call and a transcription, under 5 cents.
        </p>
      </div>
      {error && <p role="alert" className="text-sm text-destructive">{error}</p>}
      <Button
        disabled={busy}
        onClick={async () => {
          setBusy(true);
          setError(null);
          try {
            await api.createBrief(v.ad_account_id, v.footage_id, finding?.id);
            router.refresh();
          } catch (e) {
            setError(e instanceof ApiError ? e.message : 'Could not start the brief.');
            setBusy(false);
          }
        }}
      >
        {busy ? <Loader2 className="animate-spin" /> : <FileText />} Write the brief
      </Button>
    </div>
  );
}

function VariantCard({ v, editing, onChange, onRemove, render }: {
  v: BriefVariant;
  editing: boolean;
  onChange: (p: Partial<BriefVariant>) => void;
  onRemove: () => void;
  render?: React.ReactNode;
}) {
  const [copied, setCopied] = useState(false);
  return (
    <article className="flex flex-col gap-3 rounded-xl border border-border bg-card p-4">
      <div className="flex items-start gap-3">
        {editing ? (
          <Input value={v.code} aria-label="Code" className="w-16 font-semibold" onChange={(e) => onChange({ code: e.target.value })} />
        ) : (
          <span className="text-2xl leading-none font-semibold text-primary tabular-nums">{v.code}</span>
        )}
        {editing ? (
          <Input value={v.name} aria-label="Ad name" className="font-mono" onChange={(e) => onChange({ name: e.target.value })} />
        ) : (
          <button
            type="button"
            title="Copy the ad name"
            onClick={async () => {
              try {
                await navigator.clipboard.writeText(v.name);
                setCopied(true);
                setTimeout(() => setCopied(false), 1400);
              } catch {
                /* the name stays selectable */
              }
            }}
            className="group -mt-0.5 flex min-w-0 items-start gap-1.5 text-left font-mono text-sm break-all text-muted-foreground hover:text-foreground"
          >
            <span>{v.name}</span>
            {copied ? <Check className="mt-0.5 size-3.5 shrink-0" aria-hidden="true" /> : <Copy className="mt-0.5 size-3.5 shrink-0 opacity-0 group-hover:opacity-100" aria-hidden="true" />}
          </button>
        )}
      </div>
      {editing ? (
        <>
          <Textarea value={v.change} aria-label="What to change" onChange={(e) => onChange({ change: e.target.value })} />
          {v.edit && (
            <Input
              defaultValue={fmtCuts(v.edit.segments)}
              aria-label="Cuts, in play order"
              placeholder="0:30.1 to 0:35.6, 0:00.0 to 0:30.1"
              onBlur={(e) => {
                const segs = parseCuts(e.target.value);
                if (segs) onChange({ edit: { ...v.edit!, segments: segs, tight: [] } });
              }}
            />
          )}
          {v.edit?.overlays.map((o, oi) =>
            o.text === '{offer}' ? null : (
              <Input
                key={oi}
                value={o.text}
                aria-label="On-screen text"
                onChange={(e) => onChange({ edit: { ...v.edit!, overlays: v.edit!.overlays.map((x, j) => (j === oi ? { ...x, text: e.target.value } : x)) } })}
              />
            ),
          )}
          <div className="flex gap-2">
            <Input value={v.read_on} aria-label="Judge it on" onChange={(e) => onChange({ read_on: e.target.value })} />
            <Button variant="ghost" size="icon" aria-label="Remove variant" onClick={onRemove}><Trash2 /></Button>
          </div>
        </>
      ) : (
        <>
          <p className="text-sm">{v.change}</p>
          <p className="text-sm text-muted-foreground">
            <span className="font-medium text-foreground">Judge it on</span> {v.read_on.charAt(0).toLowerCase() + v.read_on.slice(1)}
          </p>
          {v.edit && v.edit.segments.length > 1 && <p className="text-sm text-muted-foreground tabular-nums">Cuts: {fmtCuts(v.edit.segments)}</p>}
        </>
      )}
      {render}
    </article>
  );
}

export function Workspace({ v }: { v: MakeVideo }) {
  const router = useRouter();
  const brief = v.brief && v.brief.status === 'done' ? v.brief : null;
  const [c, setC] = useState<BriefContent | null>(brief?.content ?? null);
  const [saved, setSaved] = useState<BriefContent | null>(brief?.content ?? null);
  const [title, setTitle] = useState(brief?.title ?? shortTitle(v.name));
  const [version, setVersion] = useState(brief?.version ?? 1);
  const [editing, setEditing] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [confirmDelete, setConfirmDelete] = useState(false);
  const [uploading, setUploading] = useState(false);
  const set = (patch: Partial<BriefContent>) => c && setC({ ...c, ...patch });

  const save = async () => {
    if (!brief || !c) return;
    setBusy(true);
    setError(null);
    try {
      const r = await api.saveBrief(brief.id, version, title, c);
      setVersion(r.version);
      setSaved(c);
      setEditing(false);
      router.refresh();
    } catch (e) {
      setError(e instanceof ApiError ? e.message : 'Could not save. Check your connection and try again.');
    } finally {
      setBusy(false);
    }
  };
  const upload = async (file: File | undefined) => {
    if (!file || !brief) return;
    setUploading(true);
    setError(null);
    try {
      await api.uploadBriefOriginal(brief.id, file);
      router.refresh();
    } catch (e) {
      setError(e instanceof ApiError ? e.message : 'Could not upload the file.');
    } finally {
      setUploading(false);
    }
  };

  const variants = c ? c.batches.flatMap((b, bi) => b.variants.map((x, vi) => ({ x, bi, vi }))) : [];
  const updVariant = (bi: number, vi: number, p: Partial<BriefVariant>) =>
    c && set({ batches: c.batches.map((b, j) => (j === bi ? { ...b, variants: b.variants.map((x, k) => (k === vi ? { ...x, ...p } : x)) } : b)) });
  const removeVariant = (bi: number, vi: number) =>
    c && set({ batches: c.batches.map((b, j) => (j === bi ? { ...b, variants: b.variants.filter((_, k) => k !== vi) } : b)) });
  const free = variants.filter(({ x }) => x.edit);
  const shoot = variants.filter(({ x }) => !x.edit);

  return (
    <div className="space-y-8">
      <div className="grid gap-6 sm:grid-cols-[180px_minmax(0,1fr)] sm:gap-8">
        <aside>
          {v.video_url ? (
            <video id={PLAYER_ID} src={v.video_url} poster={v.poster_url ?? undefined} controls preload="none" className="block aspect-[9/16] w-full rounded-xl bg-black object-contain" />
          ) : (
            <div className="aspect-[9/16] w-full rounded-xl bg-muted" />
          )}
        </aside>
        <div className="min-w-0 space-y-6">
          <header className="space-y-3">
            <div className="space-y-2">
              {editing ? (
                <Input value={title} onChange={(e) => setTitle(e.target.value)} aria-label="Title" className="max-w-xl text-base font-semibold" />
              ) : (
                <h1 className="text-2xl font-semibold tracking-tight text-balance">{title}</h1>
              )}
              <div className="flex flex-wrap items-center gap-x-3 gap-y-1 text-[13px]">
                <span className="break-all text-muted-foreground">{v.name}</span>
                {v.rep_creative_id && (
                  <Link href={`/creatives/${v.rep_creative_id}`} className="inline-flex items-center gap-1 font-medium text-primary hover:underline">
                    Creative page <ArrowUpRight className="size-3.5" aria-hidden="true" />
                  </Link>
                )}
                {brief && (
                  <label
                    title={brief.has_original ? 'Re-edits are cut from your original.' : 'Re-edits are cut from the stored copy until you upload the original.'}
                    className={cn('inline-flex cursor-pointer items-center gap-1 font-medium text-primary hover:underline focus-within:ring-2 focus-within:ring-ring', uploading && 'pointer-events-none opacity-60')}
                  >
                    {uploading ? <Loader2 className="size-3.5 animate-spin" aria-hidden="true" /> : <Upload className="size-3.5" aria-hidden="true" />}
                    {uploading ? 'Uploading' : brief.has_original ? 'Replace the original file' : 'Upload the original file'}
                    <input type="file" accept="video/*" className="sr-only" onChange={(e) => upload(e.target.files?.[0])} />
                  </label>
                )}
              </div>
              <ReasonChips reasons={v.reasons} />
            </div>
            <VerdictPanel v={v.verdict} />
          </header>
          {v.focus && <FocusCard v={v} f={v.focus} />}
        </div>
      </div>

      <Timeline v={v} />

      <div className="min-w-0 space-y-8">
        {error && <p role="alert" className="text-sm text-destructive">{error}</p>}

        {editing ? (
          <div className="space-y-10 rounded-xl border border-primary/30 p-4">
            <div className="flex flex-wrap items-center justify-between gap-3">
              <p className="text-base font-semibold">Editing the brief</p>
          {brief && c && (
            <div className="flex flex-wrap gap-2">
              {editing ? (
                <>
                  <Button onClick={save} disabled={busy}><Check /> Save</Button>
                  <Button variant="outline" onClick={() => { setC(saved); setEditing(false); setError(null); }} disabled={busy}><X /> Cancel</Button>
                </>
              ) : confirmDelete ? (
                <>
                  <Button
                    variant="destructive"
                    disabled={busy}
                    onClick={async () => {
                      setBusy(true);
                      try {
                        await api.deleteBrief(brief.id);
                        router.refresh();
                      } catch {
                        setError('Could not delete the brief.');
                      } finally {
                        setBusy(false);
                        setConfirmDelete(false);
                      }
                    }}
                  >
                    Delete the brief
                  </Button>
                  <Button variant="ghost" onClick={() => setConfirmDelete(false)}>Keep it</Button>
                </>
              ) : (
                <>
                  <Button variant="outline" onClick={() => setEditing(true)}><Pencil /> Edit the brief</Button>
                  <Button variant="ghost" size="icon" aria-label="Delete the brief" onClick={() => setConfirmDelete(true)}><Trash2 /></Button>
                </>
              )}
            </div>
          )}
            </div>
        <section className="space-y-4" aria-label="Re-edits of this file">
          <GroupHeader title="Re-edits of this file" cost="Free" tone="free">
            Cut from the video you already have: lines moved, cut or titled. Rendering costs nothing; each join sits in a pause between sentences.
          </GroupHeader>
          {v.brief && (v.brief.status === 'queued' || v.brief.status === 'running') ? (
            <div className="flex items-center gap-2 rounded-xl border border-border bg-card p-5 text-sm">
              <Loader2 className="size-4 animate-spin" aria-hidden="true" /> Writing the brief. The page updates by itself.
            </div>
          ) : v.brief?.status === 'failed' ? (
            <div className="space-y-2 rounded-xl border border-destructive/40 p-5 text-sm">
              <p className="font-medium">The brief did not finish</p>
              <p className="text-muted-foreground">{v.brief.error}</p>
            </div>
          ) : !brief || !c ? (
            <NoBrief v={v} finding={v.focus} />
          ) : free.length === 0 ? (
            <p className="text-sm text-muted-foreground">The brief has no re-edits of this file.</p>
          ) : (
            <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-3">
              {free.map(({ x, bi, vi }) => (
                <VariantCard
                  key={`${bi}-${vi}`}
                  v={x}
                  editing={editing}
                  onChange={(p) => updVariant(bi, vi, p)}
                  onRemove={() => removeVariant(bi, vi)}
                />
              ))}
            </div>
          )}
        </section>


        {brief && c && shoot.length > 0 && (
          <section className="space-y-4" aria-label="To shoot">
            <GroupHeader title="To shoot" cost="Needs a shoot" tone="shoot">
              New lines for the same creator to record at the next shoot.
            </GroupHeader>
            {shoot.length > 0 && (
              <div className="grid gap-4 sm:grid-cols-2">
                {shoot.map(({ x, bi, vi }) => (
                  <VariantCard key={`${bi}-${vi}`} v={x} editing={editing} onChange={(p) => updVariant(bi, vi, p)} onRemove={() => removeVariant(bi, vi)} />
                ))}
              </div>
            )}
          </section>
        )}

          </div>
        ) : (
          <Ideas
            v={v}
            toolbar={
              <div className="flex flex-wrap gap-2">
          {brief && c && (
            <div className="flex flex-wrap gap-2">
              {editing ? (
                <>
                  <Button onClick={save} disabled={busy}><Check /> Save</Button>
                  <Button variant="outline" onClick={() => { setC(saved); setEditing(false); setError(null); }} disabled={busy}><X /> Cancel</Button>
                </>
              ) : confirmDelete ? (
                <>
                  <Button
                    variant="destructive"
                    disabled={busy}
                    onClick={async () => {
                      setBusy(true);
                      try {
                        await api.deleteBrief(brief.id);
                        router.refresh();
                      } catch {
                        setError('Could not delete the brief.');
                      } finally {
                        setBusy(false);
                        setConfirmDelete(false);
                      }
                    }}
                  >
                    Delete the brief
                  </Button>
                  <Button variant="ghost" onClick={() => setConfirmDelete(false)}>Keep it</Button>
                </>
              ) : (
                <details className="relative">
                  <summary aria-label="More about the brief" className="inline-flex size-8 cursor-pointer list-none items-center justify-center rounded-md border border-border hover:bg-muted [&::-webkit-details-marker]:hidden">
                    <MoreHorizontal className="size-4" aria-hidden="true" />
                  </summary>
                  <div className="absolute right-0 z-30 mt-1 w-48 space-y-0.5 rounded-lg border border-border bg-popover p-1 text-sm shadow-lg shadow-foreground/5">
                    <button type="button" onClick={() => setEditing(true)} className="flex w-full items-center gap-2 rounded-md px-2 py-1.5 hover:bg-muted">
                      <Pencil className="size-4" aria-hidden="true" /> Edit the brief
                    </button>
                    <button type="button" onClick={() => setConfirmDelete(true)} className="flex w-full items-center gap-2 rounded-md px-2 py-1.5 text-destructive hover:bg-destructive/10">
                      <Trash2 className="size-4" aria-hidden="true" /> Delete the brief
                    </button>
                  </div>
                </details>
              )}
            </div>
          )}
              </div>
            }
            empty={(v.brief && (v.brief.status === 'queued' || v.brief.status === 'running') ? (
                <div className="flex items-center gap-2 rounded-xl border border-border bg-card p-5 text-sm">
                  <Loader2 className="size-4 animate-spin" aria-hidden="true" /> Writing the brief. Its ideas appear here when it is done.
                </div>
              ) : v.brief?.status === 'failed' ? (
                <div className="space-y-2 rounded-xl border border-destructive/40 p-5 text-sm">
                  <p className="font-medium">The brief did not finish</p>
                  <p className="text-muted-foreground">{v.brief.error}</p>
                </div>
              ) : !brief ? (
                <NoBrief v={v} finding={v.focus} />
              ) : null)}
          />
        )}

        {brief && c && (
          <details className="group rounded-xl border border-border" open={editing || undefined}>
            <summary className="cursor-pointer list-none px-4 py-3 text-base font-semibold marker:hidden">
              The brief in full
              <span className="ml-2 text-sm font-normal text-muted-foreground">Line-by-line map, offer, launch plan and claims review</span>
            </summary>
            <div className="space-y-8 border-t border-border px-4 py-5">
              <div className="space-y-3">
                <p className="text-sm font-medium">The video, slot by slot</p>
                <div className="overflow-x-auto">
                  <table className="w-full min-w-[560px] text-sm">
                    <thead>
                      <tr className="border-b border-border text-left text-muted-foreground">
                        <th className="py-2 pr-3 font-medium">Slot</th>
                        <th className="py-2 pr-3 font-medium">Time</th>
                        <th className="py-2 pr-3 font-medium">Said</th>
                        <th className="py-2 pr-3 font-medium">Shown</th>
                        {editing && <th className="sr-only">Remove</th>}
                      </tr>
                    </thead>
                    <tbody>
                      {c.slots.map((s, i) => {
                        const upd = (p: Partial<typeof s>) => set({ slots: c.slots.map((x, j) => (j === i ? { ...x, ...p } : x)) });
                        const hot = v.steepest_drop && s.start < v.steepest_drop.end && s.end > v.steepest_drop.start;
                        return (
                          <tr key={i} className={cn('border-b border-border align-top last:border-0', hot && !editing && 'bg-destructive/5')}>
                            <td className="py-2 pr-3 font-medium">
                              {editing ? <Input value={s.name} aria-label="Slot name" onChange={(e) => upd({ name: e.target.value })} /> : s.name}
                            </td>
                            <td className="py-2 pr-3 whitespace-nowrap tabular-nums">
                              {editing ? (
                                <div className="flex gap-1">
                                  <Input className="w-16" defaultValue={clock(s.start)} aria-label="Start" onBlur={(e) => upd({ start: parseClock(e.target.value) })} />
                                  <Input className="w-16" defaultValue={clock(s.end)} aria-label="End" onBlur={(e) => upd({ end: parseClock(e.target.value) })} />
                                </div>
                              ) : `${clock(s.start)} to ${clock(s.end)}`}
                            </td>
                            <td className="py-2 pr-3">
                              {editing ? <Textarea value={s.said} aria-label="Said" onChange={(e) => upd({ said: e.target.value })} /> : s.said && <q className="italic">{s.said}</q>}
                            </td>
                            <td className="py-2 pr-3 text-muted-foreground">
                              {editing ? <Textarea value={s.shown} aria-label="Shown" onChange={(e) => upd({ shown: e.target.value })} /> : s.shown}
                            </td>
                            {editing && (
                              <td className="py-2">
                                <Button variant="ghost" size="icon" aria-label="Remove slot" onClick={() => set({ slots: c.slots.filter((_, j) => j !== i) })}><Trash2 /></Button>
                              </td>
                            )}
                          </tr>
                        );
                      })}
                    </tbody>
                  </table>
                </div>
                {editing && (
                  <Button variant="outline" size="sm" onClick={() => set({ slots: [...c.slots, { name: '', start: 0, end: 0, said: '', shown: '' }] })}>
                    <Plus /> Add slot
                  </Button>
                )}
                <StringList items={c.findings} editing={editing} onChange={(findings) => set({ findings })} label="Finding" />
              </div>
              <div className="space-y-2">
                <label htmlFor="brief-offer" className="text-sm font-medium">Offer on the end card</label>
                {editing ? (
                  <Input id="brief-offer" value={c.offer ?? ''} placeholder="25% off and a 30-day money-back guarantee" onChange={(e) => set({ offer: e.target.value })} />
                ) : (
                  <p className="text-sm">{c.offer || '[Current offer]'}<span className="text-muted-foreground">. Used by the offer re-edit; save, then render it again.</span></p>
                )}
              </div>
              <div className="space-y-2">
                <p className="text-sm font-medium">How to launch and judge it</p>
                <StringList items={c.launch} editing={editing} ordered onChange={(launch) => set({ launch })} label="Step" />
              </div>
              <div className="space-y-2 rounded-lg bg-chart-2/10 p-4">
                <p className="text-sm font-medium">For claims review before anything ships</p>
                <StringList items={c.claims} editing={editing} onChange={(claims) => set({ claims })} label="Claim" />
              </div>
              {c.dropped && c.dropped.length > 0 && (
                <div className="space-y-1 text-sm text-muted-foreground">
                  <p className="font-medium text-foreground">Removed by checks when it was written</p>
                  <ul className="list-disc space-y-1 pl-5">{c.dropped.map((d, i) => <li key={i}>{d}</li>)}</ul>
                </div>
              )}
            </div>
          </details>
        )}

        <BatchBar v={v} />
      </div>
    </div>
  );
}
