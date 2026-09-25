'use client';
import Link from 'next/link';
import { useEffect, useRef, useState } from 'react';
import { ArrowLeft } from 'lucide-react';

const SEEK_EVENT = 'creative:seek';
const GRID_URL_KEY = 'creatives:grid-url';

// The tag taxonomy names these ratios; anything else gets the format's fallback box.
const ASPECTS: Record<string, string> = { '9:16': '9 / 16', '4:5': '4 / 5', '1:1': '1 / 1', '16:9': '16 / 9' };

// The media box reserves its space before the file loads, so nothing below it jumps. No
// dimensions are stored for assets, so the ratio comes from the aspect_ratio tag when the
// creative has one. The file is letterboxed inside the box, never cropped.
export function CreativeMedia({ src, format, alt, aspect }: { src: string | null; format: string; alt: string; aspect?: string | null }) {
  const video = useRef<HTMLVideoElement>(null);
  useEffect(() => {
    const onSeek = (e: Event) => {
      const v = video.current;
      if (!v) return;
      v.currentTime = (e as CustomEvent<number>).detail;
      void v.play().catch(() => {});
    };
    window.addEventListener(SEEK_EVENT, onSeek);
    return () => window.removeEventListener(SEEK_EVENT, onSeek);
  }, []);
  const ratio = (aspect && ASPECTS[aspect]) || (format === 'video' ? '4 / 5' : '1 / 1');
  return (
    <div
      className="flex max-h-[62dvh] w-full items-center justify-center overflow-hidden rounded-xl border border-border bg-muted"
      style={{ aspectRatio: ratio }}
    >
      {!src ? (
        <span className="text-sm text-muted-foreground">No preview stored</span>
      ) : format === 'video' ? (
        <video ref={video} src={src} controls playsInline preload="metadata" aria-label={alt} className="h-full w-full object-contain" />
      ) : (
        // eslint-disable-next-line @next/next/no-img-element -- signed blob URL, not optimizable
        <img src={src} alt={alt} fetchPriority="high" className="h-full w-full object-contain" />
      )}
    </div>
  );
}

// A timestamp that moves the page's video to that moment. The diagnosis and tags cite
// seconds; this is what makes them checkable without scrubbing by hand.
// Jump the page's video to a second. Anything on the page can call it; CreativeMedia listens.
export const seekTo = (seconds: number) => window.dispatchEvent(new CustomEvent(SEEK_EVENT, { detail: seconds }));

export function SeekButton({ seconds, label, className }: { seconds: number; label: string; className?: string }) {
  return (
    <button
      type="button"
      onClick={() => seekTo(seconds)}
      aria-label={`Play the video from ${label}`}
      className={`cursor-pointer rounded-sm tabular-nums text-primary underline-offset-2 hover:underline focus-visible:ring-2 focus-visible:ring-ring focus-visible:outline-none ${className ?? ''}`}
    >
      {label}
    </button>
  );
}

// Primary text runs to dozens of lines. Six show by default; the rest is one click away.
export function ClampedCopy({ text }: { text: string }) {
  const [open, setOpen] = useState(false);
  const long = text.length > 320;
  return (
    <div className="space-y-1">
      <p className={`text-sm whitespace-pre-line text-muted-foreground ${long && !open ? 'line-clamp-6' : ''}`}>{text}</p>
      {long && (
        <button
          type="button"
          onClick={() => setOpen(!open)}
          aria-expanded={open}
          className="cursor-pointer rounded-sm text-sm font-medium text-primary underline-offset-4 hover:underline focus-visible:ring-2 focus-visible:ring-ring focus-visible:outline-none"
        >
          {open ? 'Show less' : 'Show full text'}
        </button>
      )}
    </div>
  );
}

// The grid stores its own URL so the way back keeps the filters, sort and page.
export function RememberGridUrl() {
  useEffect(() => {
    try {
      sessionStorage.setItem(GRID_URL_KEY, window.location.pathname + window.location.search);
    } catch {
      /* storage blocked: the back link falls back to the plain grid */
    }
  });
  return null;
}

export function BackToGrid() {
  const [href, setHref] = useState('/creatives');
  useEffect(() => {
    try {
      const saved = sessionStorage.getItem(GRID_URL_KEY);
      if (saved && ['/creatives', '/campaigns'].some((p) => saved.startsWith(p))) setHref(saved);
    } catch {
      /* keep the default */
    }
  }, []);
  return (
    <Link
      href={href}
      className="inline-flex items-center gap-1.5 rounded-sm text-sm text-muted-foreground hover:text-foreground focus-visible:ring-2 focus-visible:ring-ring focus-visible:outline-none"
    >
      <ArrowLeft className="size-4" aria-hidden="true" />
      {href.startsWith('/campaigns') ? 'Campaigns' : 'Creatives'}
    </Link>
  );
}

// Copies the test brief as plain text, ready to paste into a message to the creative team.
export function CopyBrief({ text }: { text: string }) {
  const [copied, setCopied] = useState(false);
  return (
    <button
      type="button"
      onClick={async () => {
        try {
          await navigator.clipboard.writeText(text);
          setCopied(true);
          setTimeout(() => setCopied(false), 2000);
        } catch {
          /* clipboard blocked: nothing to confirm */
        }
      }}
      className="cursor-pointer rounded-md border border-border bg-background px-3 py-1.5 text-sm font-medium hover:bg-muted focus-visible:ring-2 focus-visible:ring-ring focus-visible:outline-none"
    >
      <span aria-live="polite">{copied ? 'Copied' : 'Copy as brief'}</span>
    </button>
  );
}
