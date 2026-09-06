import { clerkMiddleware } from '@clerk/nextjs/server';
import { NextResponse } from 'next/server';

export default process.env.NEXT_PUBLIC_AUTH_MODE === 'clerk' ? clerkMiddleware() : () => NextResponse.next();
export const config = { matcher: ['/((?!_next|.*\\..*).*)'] };
