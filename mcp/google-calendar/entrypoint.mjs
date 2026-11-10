import { writeFileSync } from 'node:fs';

// Keep the only credential file in tmpfs; its source remains the local .env.
const raw = process.env.GOOGLE_SERVICE_ACCOUNT_JSON;
if (!raw) {
  console.error('GOOGLE_SERVICE_ACCOUNT_JSON is required');
  process.exit(1);
}
try {
  const key = JSON.parse(raw);
  if (key.type !== 'service_account' || !key.client_email || !key.private_key) {
    throw new Error('invalid service account');
  }
  const path = '/tmp/buddy-calendar-key.json';
  writeFileSync(path, raw, { mode: 0o600 });
  process.env.GOOGLE_SERVICE_ACCOUNT_KEY = path;
  delete process.env.GOOGLE_SERVICE_ACCOUNT_JSON;
} catch {
  console.error('Invalid GOOGLE_SERVICE_ACCOUNT_JSON');
  process.exit(1);
}
await import('@cocal/google-calendar-mcp/build/index.js');
