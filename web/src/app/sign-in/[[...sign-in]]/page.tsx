import { SignIn } from '@clerk/nextjs';

// Not protected by requireAuth(): it's the redirect target for every other
// page, so protecting it would redirect-loop against itself.
export default function SignInPage() {
  return (
    <div className="flex min-h-[70vh] items-center justify-center">
      <SignIn />
    </div>
  );
}
