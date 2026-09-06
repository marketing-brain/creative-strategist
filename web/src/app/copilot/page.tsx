import { CopilotChat } from '@/components/copilot-chat';
import { api } from '@/lib/api';

export default async function Copilot() {
  const conversations = await api.conversations();
  return <div className="space-y-4"><h1 className="text-xl font-semibold">Copilot</h1><CopilotChat conversations={conversations} initialId={conversations[0]?.id ?? null} /></div>;
}
