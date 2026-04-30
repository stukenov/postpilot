import fs from 'node:fs/promises';
import path from 'node:path';
import process from 'node:process';
import { chromium } from 'playwright';

const DEFAULT_OUTPUT = 'state/facebook-browser-auth.json';
const LOGIN_URL = 'https://www.facebook.com/login';
const LOGIN_TIMEOUT_MS = 5 * 60 * 1000; // 5 minutes

function parseArgs(argv) {
  const options = {
    output: path.resolve(process.cwd(), DEFAULT_OUTPUT),
  };

  for (let i = 0; i < argv.length; i += 1) {
    const arg = argv[i];
    switch (arg) {
      case '--output':
        options.output = path.resolve(process.cwd(), argv[++i]);
        break;
      default:
        if (arg.startsWith('--')) {
          throw new Error(`Unknown argument: ${arg}`);
        }
        options.output = path.resolve(process.cwd(), arg);
    }
  }

  return options;
}

async function main() {
  const options = parseArgs(process.argv.slice(2));
  await fs.mkdir(path.dirname(options.output), { recursive: true });

  const browser = await chromium.launch({
    headless: false,
  });

  try {
    const context = await browser.newContext({ viewport: { width: 1440, height: 980 } });
    const page = await context.newPage();

    await page.goto(LOGIN_URL, { waitUntil: 'domcontentloaded' });
    console.log('Log in to Facebook in the browser window. Waiting...');

    // Wait for actual feed — skip /login, /checkpoint (OTP/2FA), /recover, etc.
    await page.waitForURL(url => {
      const p = url.pathname;
      return url.hostname.includes('facebook.com')
        && !p.startsWith('/login')
        && !p.startsWith('/checkpoint')
        && !p.startsWith('/recover')
        && !p.startsWith('/two_step_verification');
    }, { timeout: LOGIN_TIMEOUT_MS });
    console.log('Login detected. Saving storage state...');

    await context.storageState({ path: options.output });
    await context.close();
  } finally {
    await browser.close();
  }

  console.log(JSON.stringify({ storageState: options.output }, null, 2));
}

main().catch((error) => {
  console.error(error);
  process.exit(1);
});
