# Build context: the repository root (the demo links clients/react/obs-telemetry).
#   docker build -f examples/react-demo/Dockerfile -t obs-react-demo .

FROM node:22-alpine AS build

# Library: install + build (layout mirrors the repo so the file: dependency resolves).
WORKDIR /src/clients/react/obs-telemetry
COPY clients/react/obs-telemetry/package.json clients/react/obs-telemetry/package-lock.json ./
RUN npm ci
COPY clients/react/obs-telemetry/tsconfig.json clients/react/obs-telemetry/tsconfig.build.json ./
COPY clients/react/obs-telemetry/src ./src
RUN npm run build

# Demo app.
WORKDIR /src/examples/react-demo
COPY examples/react-demo/package.json examples/react-demo/package-lock.json ./
RUN npm ci
COPY examples/react-demo/index.html examples/react-demo/tsconfig.json examples/react-demo/vite.config.ts ./
COPY examples/react-demo/src ./src
ARG VITE_DEMO_TOKEN=""
RUN VITE_DEMO_TOKEN="$VITE_DEMO_TOKEN" npm run build

FROM nginxinc/nginx-unprivileged:1.27-alpine
COPY examples/react-demo/nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=build /src/examples/react-demo/dist /usr/share/nginx/html
EXPOSE 8080
