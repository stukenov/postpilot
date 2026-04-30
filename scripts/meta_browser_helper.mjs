import { chromium } from 'playwright';
import path from 'node:path';
import process from 'node:process';

const profileDir = path.resolve(process.cwd(), '.playwright-meta-profile');
const chromeExecutable = '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome';
const startUrl = process.argv[2] || 'https://developers.facebook.com/apps/';

async function main() {
  const context = await chromium.launchPersistentContext(profileDir, {
    executablePath: chromeExecutable,
    headless: false,
    viewport: { width: 1440, height: 980 },
    args: ['--start-maximized']
  });

  const page = context.pages()[0] || await context.newPage();
  await page.goto(startUrl, { waitUntil: 'domcontentloaded' });

  console.log(`Meta browser helper opened: ${startUrl}`);
  console.log('Use this Chrome window for login/authorization. Keep this process running.');
  console.log('When you are done with the browser, close the window or press Ctrl+C here.');

  await new Promise(() => {});
}

main().catch((error) => {
  console.error(error);
  process.exit(1);
});
