import type { NextConfig } from "next";
import path from "node:path";

const nextConfig: NextConfig = {
  webpack: (config, { isServer }) => {
    // Route `./auth-headers` (imported from src/lib/api.ts) to the Node-only
    // implementation, but ONLY for modules whose issuer sits in Next's 'rsc'
    // webpack layer (true React Server Components, e.g. app/*/page.tsx).
    //
    // Why not a plain top-level `resolve.alias` guarded by `isServer`: the
    // "isServer" (nodejs) webpack compiler also traces the module graph
    // reachable from 'use client' components (e.g. accounts-panel.tsx -> api.ts)
    // in order to build the client reference manifest for SSR/hydration. A
    // global alias can't distinguish "api.ts imported by a real Server
    // Component" from "api.ts imported by a Client Component being traced
    // inside the same server compiler" — aliasing globally made the client
    // boundary check trip on the *client* layer instead. Scoping the alias to
    // `issuerLayer: 'rsc'` (see next/dist/lib/constants.js WEBPACK_LAYERS)
    // means only genuine Server Component imports get the Clerk-server file;
    // everything reached from a 'use client' boundary keeps resolving to the
    // default `auth-headers.ts`, which never references
    // '@clerk/nextjs/server' and is therefore safe to bundle for the client.
    if (isServer) {
      config.module.rules.push({
        issuerLayer: "rsc",
        resolve: {
          alias: {
            "./auth-headers$": path.resolve(__dirname, "src/lib/auth-headers.server.ts"),
            "@/lib/auth-headers$": path.resolve(__dirname, "src/lib/auth-headers.server.ts"),
          },
        },
      });
    }
    return config;
  },
};

export default nextConfig;
