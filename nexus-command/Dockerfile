FROM node:20-slim AS builder

WORKDIR /app

COPY package.json package-lock.json ./
RUN npm ci --legacy-peer-deps

COPY . .
RUN npm run build:all

FROM node:20-slim AS runner

WORKDIR /app

COPY package.json package-lock.json ./
RUN npm ci --omit=dev --legacy-peer-deps

COPY --from=builder /app/dist ./dist
COPY --from=builder /app/dist-server ./dist-server
COPY --from=builder /app/server/operational/migrations ./dist-server/operational/migrations

ENV NODE_ENV=production
EXPOSE 3000

# Live operations require DATABASE_URL. Without it, Nexus starts in an explicit
# configuration-required state; it never falls back to review data in production.
CMD ["sh", "-c", "if [ -n \"$DATABASE_URL\" ]; then node dist-server/operational/migrate.js; fi; exec node dist-server/index.js"]
