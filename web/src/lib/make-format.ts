// Plain helpers for the Make tab, usable from server and client components alike
// (a 'use client' module's functions cannot be called during server rendering).

export const clock = (s: number) => {
  const t = Math.max(0, Math.round(s));
  return `${Math.floor(t / 60)}:${String(t % 60).padStart(2, '0')}`;
};
export const clock1 = (s: number) => {
  const t = Math.max(0, s);
  return `${Math.floor(t / 60)}:${(t % 60).toFixed(1).padStart(4, '0')}`;
};
export const parseClock = (v: string): number => {
  const m = v.trim().match(/^(\d+):(\d{1,2})(?:\.(\d))?$/);
  if (m) return Number(m[1]) * 60 + Number(m[2]) + (m[3] ? Number(m[3]) / 10 : 0);
  const n = Number(v);
  return Number.isFinite(n) ? n : 0;
};
export const money = (x: number) => `$${Math.round(x).toLocaleString()}`;
export const money2 = (x: number) => `$${x.toFixed(2)}`;
export const pct = (x: number) => `${Math.round(x * 100)}%`;
export const signedPct = (x: number) => `${x >= 0 ? '+' : '−'}${Math.abs(Math.round(x * 100))}%`;

/** A short human title from an ad name: the concept token (CamelCase, e.g. NotASleepAid),
 *  else the creator's handle, else the last descriptive token. */
export function shortTitle(name: string): string {
  const tokens = name
    .replace(/\s*[–-]\s*Copy(\s*\d+)?$/i, '')
    .split(/_|\s-\s/)
    .map((t) => t.trim())
    .filter(Boolean);
  const spaced = (t: string) => t.replace(/([a-z])([A-Z])/g, '$1 $2').replace(/([A-Z]+)([A-Z][a-z])/g, '$1 $2');
  const isDate = (t: string) => /^\d{1,2}(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)/i.test(t);
  const noise = (t: string) => /^(TT|META|v|R)\d/i.test(t) || /^by[A-Z]/.test(t) || /^(tiktok|bca|ugc|magashwa|copy)$/i.test(t);
  const camel = [...tokens].reverse().find(
    (t) => !isDate(t) && !noise(t) && !/\s/.test(t) && t.length >= 6 && /[a-z]/.test(t) && (t.match(/[A-Z]/g) ?? []).length >= 2,
  );
  if (camel) return spaced(camel);
  const handle = tokens.find((t) => /^[a-z][a-z0-9._]{4,}$/.test(t) && !noise(t));
  if (handle) return handle;
  const last = [...tokens].reverse().find((t) => /[a-z]/i.test(t) && t.length > 3 && !isDate(t) && !noise(t) && !/^\d+$/.test(t));
  return last ? spaced(last) : name;
}
