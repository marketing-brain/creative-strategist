import { redirect } from 'next/navigation';

// Briefs moved into Make (2026-09-26): one page per video holds its brief, renders and generations.
export default function Briefs() {
  redirect('/make');
}
