'use client';
import { useState } from 'react';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Textarea } from '@/components/ui/textarea';
import { ApiError, api, apiFetch } from '@/lib/api';
import type { BrandKit } from '@/lib/types';

const list = (s: string, sep: RegExp) => s.split(sep).map((x) => x.trim()).filter(Boolean);

export function BrandKitForm({ initial }: { initial: BrandKit | null }) {
  const [colors, setColors] = useState(initial?.colors.join(', ') ?? '');
  const [fonts, setFonts] = useState(initial?.fonts.join(', ') ?? '');
  const [voice, setVoice] = useState(initial?.voice_rules ?? '');
  const [prohibited, setProhibited] = useState(initial?.prohibited_claims.join('\n') ?? '');
  const [logoUrl, setLogoUrl] = useState(initial?.logo_url ?? null);
  const [msg, setMsg] = useState<string | null>(null);

  const save = async () => {
    try {
      const kit = await api.saveBrandKit({ colors: list(colors, /,/), fonts: list(fonts, /,/), voice_rules: voice, prohibited_claims: list(prohibited, /\n/) });
      setMsg(`Saved version ${kit.version}.`);
    } catch (e) {
      if (e instanceof ApiError && e.status === 403) {
        setMsg("You don't have permission to do this — ask an admin.");
      } else {
        setMsg(`Failed to save: ${e instanceof Error ? e.message : 'Something went wrong.'}`);
      }
    }
  };
  const upload = async (file: File) => {
    try {
      const fd = new FormData(); fd.append('file', file);
      const kit = await apiFetch<BrandKit>('/brand-kit/logo', { method: 'POST', body: fd });
      setLogoUrl(kit.logo_url); setMsg(`Logo saved (version ${kit.version}).`);
    } catch (e) {
      if (e instanceof ApiError && e.status === 403) {
        setMsg("You don't have permission to do this — ask an admin.");
      } else {
        setMsg(`Failed to upload logo: ${e instanceof Error ? e.message : 'Something went wrong.'}`);
      }
    }
  };

  return (
    <section className="space-y-3">
      <h2 className="text-lg font-semibold">Brand kit</h2>
      <label className="block text-sm">Colors (comma separated)<Input value={colors} onChange={(e) => setColors(e.target.value)} placeholder="#112233, #FFFFFF" /></label>
      <label className="block text-sm">Fonts (comma separated)<Input value={fonts} onChange={(e) => setFonts(e.target.value)} /></label>
      <label className="block text-sm">Voice rules<Textarea value={voice} onChange={(e) => setVoice(e.target.value)} /></label>
      <label className="block text-sm">Prohibited claims (one per line)<Textarea value={prohibited} onChange={(e) => setProhibited(e.target.value)} /></label>
      <label className="block text-sm">Logo (PNG or SVG)<Input type="file" accept="image/png,image/svg+xml" onChange={(e) => e.target.files?.[0] && upload(e.target.files[0])} /></label>
      {logoUrl && <img src={logoUrl} alt="logo" className="h-12" />}
      <Button onClick={save}>Save brand kit</Button>
      {msg && <p role="status" className="text-sm">{msg}</p>}
    </section>
  );
}
