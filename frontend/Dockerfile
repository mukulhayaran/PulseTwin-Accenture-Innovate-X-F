# Static export: the built site is plain files, so the runtime image is just a webserver.
FROM node:22-alpine AS build
WORKDIR /app
COPY package.json package-lock.json* ./
RUN npm ci --no-audit --no-fund || npm install --no-audit --no-fund
COPY . .
RUN npm run build

FROM node:22-alpine
WORKDIR /app
RUN npm install -g serve@14
COPY --from=build /app/out ./out
EXPOSE 3000
# NOT `serve -s`. `-s`/`--single` is the single-page-app mode: it rewrites EVERY path that
# is not an exact file hit to index.html. Next's static export is multi-page -- it emits
# supervisor.html, manager.html, leadership.html, ops.html, login.html -- and serve@14
# applies the single-file rewrite in preference to its own clean-URL extension lookup, so
# `-s` served the LANDING page for all five persona routes (verified: /supervisor and /
# returned byte-identical 8,256-byte responses). Without it each route resolves to its own
# file via clean URLs. Never reproduced in dev, because `next dev` routes these itself.
CMD ["serve", "out", "-l", "3000"]
