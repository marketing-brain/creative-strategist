// How a creative's number is compared with its account, shared by the grid and the creative
// page so a figure that reads "below the account" on a card reads the same once opened.

// The plain arithmetic gap to the baseline, or null when either side is missing.
export const gap = (mine: number | null, base: number | null) => (mine == null || !base ? null : mine / base - 1);

// A comparison shows only where the diagnosis would make one: 1,000 impressions for the
// attention numbers, 100 link clicks for the conversion ones, on both sides. "ROAS 0.00x,
// -100% vs account" on four dollars of spend is noise dressed as a finding.
export const IMPRESSION_FLOOR = 1000;
export const CLICK_FLOOR = 100;

// Gaps under 15% stay grey, the diagnosis's own bar for calling a layer weak or strong.
export const GAP_BAR = 0.15;

export function gapTone(delta: number | null | undefined, higherIsBetter: boolean, neutral = false) {
  if (neutral || delta == null || Math.abs(delta) < GAP_BAR) return 'text-muted-foreground';
  return (higherIsBetter ? delta > 0 : delta < 0) ? 'text-success' : 'text-destructive';
}

// Signed as a buyer reads it: cost down is minus. The colour, not the sign, says good or bad.
export const gapText = (delta: number) => `${delta >= 0 ? '+' : '−'}${Math.round(Math.abs(delta) * 100)}%`;
