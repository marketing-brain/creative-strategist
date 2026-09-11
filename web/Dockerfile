# NEXT_PUBLIC_* vars are baked into the client bundle at `npm run build` time -- same
# constraint deploy/ec2/setup.sh's web/.env.local comment calls out. Render forwards a
# Docker service's configured env vars as build args automatically, matched by name to
# the ARGs declared below, so these must be set as env vars on the cs-web service in
# render.yaml (or the Render dashboard) -- not just at runtime.
FROM node:22-slim AS deps
WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci

FROM node:22-slim AS build
WORKDIR /app
COPY --from=deps /app/node_modules ./node_modules
COPY . .
ARG NEXT_PUBLIC_API_URL
ARG NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY
ENV NEXT_PUBLIC_API_URL=$NEXT_PUBLIC_API_URL \
    NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY=$NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY
RUN npm run build

FROM node:22-slim AS run
WORKDIR /app
ENV NODE_ENV=production
# CLERK_SECRET_KEY (the Next.js SERVER reads this at runtime, unlike the NEXT_PUBLIC_*
# vars above) comes from render.yaml's envVars, not this Dockerfile.
COPY --from=build /app/next.config.ts ./
COPY --from=build /app/public ./public
COPY --from=build /app/.next ./.next
COPY --from=build /app/node_modules ./node_modules
COPY --from=build /app/package.json ./package.json
EXPOSE 3000
CMD ["npm", "start"]
