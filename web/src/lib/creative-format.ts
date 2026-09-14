import type { Creative } from '@/lib/types';

export type FormatKind = 'image' | 'video' | 'carousel' | 'unknown';

export type FormatStatus = {
  label: string;
  kind: FormatKind;
  hasFile: boolean;
  /** Why the file or format is missing; null when there is nothing to explain. */
  explanation: string | null;
};

const names = { image: 'Image', video: 'Video', carousel: 'Carousel' } as const;

const missingFile = {
  video:
    "Meta's data shows this is a video, but its file isn't available to download. Usually it was made only for the ad, and Meta doesn't publish those anywhere we can fetch a copy from.",
  image: "Meta's data shows this is an image, but its file hasn't been downloaded yet.",
  carousel: "Meta's data shows this is a carousel, but its card images haven't been downloaded yet.",
} as const;

/**
 * What to call a creative in the UI. `format` is what analysis uses: image and video are only
 * stored once the file is. `evidence_format` is what Meta's data proves, file or not, so a
 * proven video without a file is shown as a video rather than as an unknown.
 */
export function formatStatus(c: Pick<Creative, 'format' | 'evidence_format' | 'primary_asset_url'>): FormatStatus {
  if (c.format !== 'unresolved') {
    return { label: names[c.format], kind: c.format, hasFile: c.primary_asset_url != null, explanation: null };
  }
  if (c.evidence_format !== 'unresolved') {
    return {
      label: `${names[c.evidence_format]} · no file`,
      kind: c.evidence_format,
      hasFile: false,
      explanation: missingFile[c.evidence_format],
    };
  }
  return {
    label: 'Unknown format',
    kind: 'unknown',
    hasFile: false,
    explanation: "Meta's data doesn't show whether this is an image or a video, so it's left out of format comparisons instead of guessed.",
  };
}
