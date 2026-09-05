# Servicio web de MeteoLabX: SvelteKit con renderizado en servidor.
#
# Va en su propio servicio de Railway, separado del Python. El build necesita
# Node y nada más; el catálogo, los proveedores y el worker de predicción
# siguen en el servicio antiguo, al que este llega por HTTP.
FROM node:22-alpine AS build
WORKDIR /app

# Capa de dependencias aparte: mientras el lock no cambie, un despliegue que
# solo toca componentes no vuelve a instalar nada.
COPY package.json package-lock.json ./
RUN npm ci

COPY . .
RUN npm run build

FROM node:22-alpine AS runtime
WORKDIR /app
ENV NODE_ENV=production

COPY package.json package-lock.json ./
RUN npm ci --omit=dev && npm cache clean --force

COPY --from=build /app/build ./build
# `server.js` importa la frontera de rutas desde `src/`, así que el módulo
# tiene que viajar también a la imagen final.
COPY server.js ./
COPY src/lib/legacy-path.js ./src/lib/legacy-path.js
COPY src/lib/seo ./src/lib/seo

EXPOSE 3000
CMD ["node", "server.js"]
