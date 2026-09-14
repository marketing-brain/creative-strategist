import { describe, expect, it } from 'vitest';
import { formatStatus } from '@/lib/creative-format';

describe('formatStatus', () => {
  it('uses the stored format for a creative with its file', () => {
    const s = formatStatus({ format: 'video', evidence_format: 'video', primary_asset_url: '/blobs/v.mp4' });
    expect(s).toMatchObject({ label: 'Video', kind: 'video', hasFile: true });
    expect(s.explanation).toBeNull();
  });

  it('names a proven video that has no file instead of calling it unknown', () => {
    const s = formatStatus({ format: 'unresolved', evidence_format: 'video', primary_asset_url: null });
    expect(s).toMatchObject({ label: 'Video · no file', kind: 'video', hasFile: false });
    expect(s.explanation).toMatch(/made only for the ad/);
  });

  it('names a proven image that has no file yet', () => {
    const s = formatStatus({ format: 'unresolved', evidence_format: 'image', primary_asset_url: null });
    expect(s).toMatchObject({ label: 'Image · no file', kind: 'image', hasFile: false });
  });

  it('calls a creative unknown only when the data proves no format', () => {
    const s = formatStatus({ format: 'unresolved', evidence_format: 'unresolved', primary_asset_url: null });
    expect(s).toMatchObject({ label: 'Unknown format', kind: 'unknown', hasFile: false });
    expect(s.explanation).toMatch(/image or a video/);
  });
});
