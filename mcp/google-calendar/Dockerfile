FROM node:22-alpine
WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci --omit=dev --ignore-scripts --no-audit --no-fund
# Upstream v2.8.1 registers manage-accounts outside ENABLED_TOOLS.
# Fail the build if the pinned implementation changes; expose no account-write tool.
RUN node -e 'const fs=require("fs"); const p="node_modules/@cocal/google-calendar-mcp/build/index.js"; const s=fs.readFileSync(p,"utf8"); const needle="this.registerAccountManagementTools(server);"; if(s.split(needle).length!==2)throw Error("Review upstream account registration"); fs.writeFileSync(p,s.replace(needle,"/* Account management disabled for read-only AI-buddy. */"));'
COPY entrypoint.mjs /app/entrypoint.mjs
USER node
ENV TRANSPORT=http HOST=0.0.0.0 PORT=3000
EXPOSE 3000
ENTRYPOINT ["node", "/app/entrypoint.mjs"]
