import { redirect } from 'next/navigation';

// New videos moved to /new-video (2026-09-27); old links land on the same kind.
export default async function OldKind({ params }: { params: Promise<{ kind: string }> }) {
  redirect(`/new-video/${(await params).kind}`);
}
