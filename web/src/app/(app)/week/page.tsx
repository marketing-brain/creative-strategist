import { redirect } from 'next/navigation';

// This week was removed (2026-09-26); old links land on Ads.
export default function Week() {
  redirect('/creatives');
}
