import { describe, expect, it } from 'vitest';
import { blobUrl } from '@/lib/blob-url';

describe('blobUrl', () => {
  it('rewrites an absolute backend blob url to a same-origin proxy path', () => {
    expect(blobUrl('http://localhost:8000/blobs/org_1/abc.png')).toBe('/blobs/org_1/abc.png');
  });

  it('leaves an already-relative proxy path alone', () => {
    expect(blobUrl('/blobs/org_1/abc.png')).toBe('/blobs/org_1/abc.png');
  });

  it('passes through urls that are not blob urls (e.g. s3 presigned)', () => {
    const s3 = 'https://bucket.s3.amazonaws.com/org_1/abc.png?X-Amz-Signature=x';
    expect(blobUrl(s3)).toBe(s3);
  });

  it('passes through null and undefined', () => {
    expect(blobUrl(null)).toBeNull();
    expect(blobUrl(undefined)).toBeUndefined();
  });
});
