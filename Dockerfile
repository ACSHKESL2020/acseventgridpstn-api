# Node 20 base image for Azure Container Apps
FROM node:20-alpine

# Create app directory
WORKDIR /usr/src/app

# Install dependencies
COPY package.json package-lock.json* ./
RUN npm ci || npm install --no-audit --no-fund

# Bundle app source
COPY . .

# Set environment
ENV NODE_ENV=production
# Azure Container Apps will set PORT
EXPOSE 8080

# Run the web service on container startup.
CMD ["npm", "start"]
