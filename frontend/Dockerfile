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
CMD ["serve", "-s", "out", "-l", "3000"]
