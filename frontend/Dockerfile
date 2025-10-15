
# Multi-stage build for smallest image
FROM node:20-alpine AS build
ARG APP_VERSION="dev"
ARG GIT_SHA="unknown"
WORKDIR /app
COPY package.json package-lock.json* ./
RUN npm ci --prefer-offline --no-audit --progress=false
COPY . .
RUN npm run build

FROM nginx:alpine AS prod
ARG APP_VERSION="dev"
ARG GIT_SHA="unknown"
RUN apk add --no-cache netcat-openbsd
COPY --from=build /app/dist /usr/share/nginx/html
COPY nginx.conf /etc/nginx/conf.d/default.conf
COPY wait-for-backend.sh /wait-for-backend.sh
RUN chmod +x /wait-for-backend.sh
EXPOSE 80
LABEL org.opencontainers.image.title="WatchBuddy Frontend" \
	  org.opencontainers.image.version="$APP_VERSION" \
	  org.opencontainers.image.revision="$GIT_SHA" \
	  org.opencontainers.image.source="https://github.com/steffenbb/WatchBuddy"

ENV APP_VERSION=$APP_VERSION \
	GIT_SHA=$GIT_SHA
ENTRYPOINT ["/wait-for-backend.sh"]
