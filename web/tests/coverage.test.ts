import { describe, expect, it } from 'vitest';
import { filesRead, spendShare, tagStatusLine, untaggedNote } from '@/lib/coverage';
import type { Coverage } from '@/lib/types';

const cov = (over: Partial<Coverage> = {}): Coverage => ({ files: 363, tagged_files: 170, spend_share: 0.952, ...over });

describe('spendShare', () => {
  it('floors, so 99.5% never reads as all of it', () => {
    expect(spendShare(cov({ spend_share: 0.995 }), 56)).toBe('99% of account spend');
  });

  it('states the share the read covered', () => {
    expect(spendShare(cov(), 56)).toBe('95% of account spend');
  });

  it('says nothing when the read saw every file', () => {
    expect(spendShare(cov({ files: 56 }), 56)).toBeNull();
  });

  it('says nothing when the share is unknown', () => {
    // An empty file set yields no share: borrowing today's number is the "99%" bug.
    expect(spendShare(cov({ spend_share: null }), 56)).toBeNull();
    expect(spendShare(null, 56)).toBeNull();
    expect(spendShare(undefined, 56)).toBeNull();
  });
});

describe('filesRead', () => {
  it('names both the read and the account', () => {
    expect(filesRead(cov(), 56)).toBe('56 of 363 videos');
  });

  it('drops the comparison when there is nothing more to see', () => {
    expect(filesRead(cov({ files: 56 }), 56)).toBe('56 videos');
    expect(filesRead(null, 56)).toBe('56 videos');
  });
});

describe('untaggedNote', () => {
  it('counts what a tag filter cannot reach', () => {
    expect(untaggedNote(cov())).toBe('193 of 363 files are untagged and cannot match a tag filter.');
  });

  it('says nothing when every file is tagged', () => {
    expect(untaggedNote(cov({ tagged_files: 363 }))).toBeNull();
  });

  it('says nothing when the counts are missing', () => {
    expect(untaggedNote({})).toBeNull();
    expect(untaggedNote(null)).toBeNull();
  });
});

describe('tagStatusLine', () => {
  it('gives the gate’s reason', () => {
    expect(tagStatusLine({ reason: 'below_threshold', detail: 'delivered 39 impressions and 36 plays, under the 2,000 impressions or 1,000 plays a comparison needs' })).toBe(
      'Not tagged — delivered 39 impressions and 36 plays, under the 2,000 impressions or 1,000 plays a comparison needs.',
    );
  });

  it('says a file is waiting only when nothing refused it', () => {
    expect(tagStatusLine({ reason: null, detail: null })).toBe('Queued for tagging.');
    expect(tagStatusLine(undefined)).toBe('Queued for tagging.');
  });
});
