// What a page says about the files it could not see.
//
// Both lines below have been wrong in ways that read as confident: the scope line once
// claimed "99% of account spend" beside a read that covered 95%, because the two halves came
// from different moments. They live here, away from the pages, so they can be tested.
import type { Coverage, TagStatus } from './types';

/** The share of account spend a read covered, floored. 99.5% must not read as "all of it". */
export function spendShare(coverage: Coverage | null | undefined, filesRead: number): string | null {
  const total = coverage?.files;
  const share = coverage?.spend_share;
  if (!total || total <= filesRead || share == null) return null;
  return `${Math.floor(share * 100)}% of account spend`;
}

/** "56 of 363 videos", or plain "56 videos" when the read saw everything there is. */
export function filesRead(coverage: Coverage | null | undefined, filesRead: number, noun = 'videos'): string {
  const total = coverage?.files;
  return total && total > filesRead ? `${filesRead} of ${total} ${noun}` : `${filesRead} ${noun}`;
}

/** Why a tag filter matches fewer files than the account holds, or nothing to say. */
export function untaggedNote(coverage: Coverage | null | undefined): string | null {
  const total = coverage?.files;
  const tagged = coverage?.tagged_files;
  if (total == null || tagged == null || total - tagged <= 0) return null;
  return `${total - tagged} of ${total} files are untagged and cannot match a tag filter.`;
}

/** Why this creative has no tags: the gate's own reason, or that it is simply waiting. */
export function tagStatusLine(status: TagStatus | null | undefined): string {
  return status?.detail ? `Not tagged — ${status.detail}.` : 'Queued for tagging.';
}
