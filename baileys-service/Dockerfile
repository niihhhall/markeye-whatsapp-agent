FROM node:20-slim
RUN apt-get update && apt-get install -y git
WORKDIR /app
COPY package.json .
RUN npm install
COPY . .
CMD ["node", "index.js"]
