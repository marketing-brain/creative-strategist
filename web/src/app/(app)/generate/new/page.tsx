import { redirect } from 'next/navigation';

export default async function NewGeneration({ searchParams }: { searchParams: Promise<{ footage?: string }> }) {
  const { footage } = await searchParams;
  redirect(footage ? `/make/${footage}` : '/make');
}
