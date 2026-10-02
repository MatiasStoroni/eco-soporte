FROM node:22-alpine
WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci
COPY index.html admin.html vite.config.js ./
COPY src ./src
RUN npm run build

ENV API_URL=http://api:8000
EXPOSE 8080
CMD ["npm", "run", "preview"]
