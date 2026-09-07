import { CopilotChat } from '@/components/copilot-chat';
import { api } from '@/lib/api';
import { requireAuth } from '@/lib/require-auth.server';

export const metadata = { title: 'Copilot' };

export default async function Copilot() {
  await requireAuth();
  const conversations = await api.conversations();
  return (
    <div className="flex h-full flex-col space-y-4">
      <div>
        <h1 className="text-2xl font-semibold tracking-tight">Copilot</h1>
        <p className="text-sm text-muted-foreground">Ask questions about your creative performance in plain language.</p>
      </div>
      <CopilotChat conversations={conversations} initialId={conversations[0]?.id ?? null} />
    </div>
  );
}
