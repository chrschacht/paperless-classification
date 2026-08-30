# Build stage
# The frontend bundle is architecture-independent, so build it once on the
# native CI runner instead of repeating the Node build through QEMU.
FROM --platform=$BUILDPLATFORM node:20-alpine AS build

WORKDIR /app

# Copy package files
COPY package*.json ./

# Install dependencies
RUN npm ci

# Copy source code
COPY . .

# Build the application
RUN npm run build

# Production stage
FROM nginx:alpine

LABEL org.opencontainers.image.source="https://github.com/chrschacht/paperless-classification"

# Copy custom nginx config
COPY nginx.conf /etc/nginx/conf.d/default.conf

# Copy built assets from build stage
COPY --from=build /app/dist /usr/share/nginx/html

# Expose port 80
EXPOSE 80

CMD ["nginx", "-g", "daemon off;"]
