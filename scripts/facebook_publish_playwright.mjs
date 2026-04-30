import fs from 'node:fs/promises';
import path from 'node:path';
import process from 'node:process';
import { chromium } from 'playwright';

const FEED_URL = 'https://www.facebook.com/';

const DEBUG_ATTEMPT_DIR = process.env.FACEBOOK_DEBUG_ATTEMPT_DIR
  ? path.resolve(process.env.FACEBOOK_DEBUG_ATTEMPT_DIR)
  : null;

let activePage = null;

function parseArgs(argv) {
  const options = {
    file: null,
    storageState: null,
    headless: false,
  };

  for (let i = 0; i < argv.length; i += 1) {
    const arg = argv[i];
    switch (arg) {
      case '--storage-state':
        options.storageState = path.resolve(process.cwd(), argv[++i]);
        break;
      case '--headless':
        options.headless = true;
        break;
      case '--headed':
        options.headless = false;
        break;
      default:
        if (arg.startsWith('--')) {
          throw new Error(`Unknown argument: ${arg}`);
        }
        if (options.file) {
          throw new Error('Only one text file is allowed');
        }
        options.file = path.resolve(process.cwd(), arg);
    }
  }

  if (!options.file) {
    throw new Error('Usage: node scripts/facebook_publish_playwright.mjs [options] <txt-file>');
  }
  if (!options.storageState) {
    throw new Error('--storage-state is required');
  }

  return options;
}

async function saveDebugScreenshot(page, filename) {
  if (!DEBUG_ATTEMPT_DIR) return;
  try {
    await fs.mkdir(DEBUG_ATTEMPT_DIR, { recursive: true });
    await page.screenshot({
      path: path.join(DEBUG_ATTEMPT_DIR, filename),
      fullPage: true,
    });
  } catch (err) {
    console.error('Debug screenshot failed:', err.message);
  }
}

async function openComposer(page) {
  await page.goto(FEED_URL, { waitUntil: 'domcontentloaded' });
  await page.waitForTimeout(3000);

  // Click composer trigger — "What's on your mind?" (EN) / "Что у вас нового" (RU)
  const composerTrigger = page.locator('[role="button"]').filter({
    hasText: /What.s on your mind|Что у вас нового/,
  });
  await composerTrigger.first().click();

  // Wait for composer textbox to appear
  await page.waitForSelector('div[role="textbox"][contenteditable="true"]', { timeout: 15000 });
}

async function fillPost(page, text) {
  const editor = page.locator('div[role="textbox"][contenteditable="true"]').first();
  await editor.click();
  await editor.fill(text);
  await page.waitForTimeout(500);
}

async function submitPost(page) {
  // Step 1: Click "Next" (EN) / "Далее" (RU)
  const nextButton = page.locator('div[role="dialog"] [role="button"]').filter({
    hasText: /^(Next|Далее)$/,
  }).first();
  await nextButton.click();
  await page.waitForTimeout(2000);

  // Step 2: Click "Post" (EN) / "Опубликовать" (RU)
  const postButton = page.locator('div[role="dialog"] [role="button"][aria-label]').filter({
    hasText: /^(Post|Опубликовать)$/,
  }).first();
  await postButton.click();

  // Wait for dialog to close
  await page.waitForFunction(
    () => !document.querySelector('div[role="dialog"] div[role="textbox"]'),
    { timeout: 30000 },
  );
  await page.waitForTimeout(3000);
}

async function main() {
  const options = parseArgs(process.argv.slice(2));
  const text = (await fs.readFile(options.file, 'utf-8')).trim();

  if (!text) {
    throw new Error(`Text file is empty: ${options.file}`);
  }

  const browser = await chromium.launch({ headless: options.headless });
  const context = await browser.newContext({
    storageState: options.storageState,
    viewport: { width: 1440, height: 980 },
  });
  const page = await context.newPage();
  activePage = page;

  try {
    await openComposer(page);
    await fillPost(page, text);
    await saveDebugScreenshot(page, 'before-submit.png');
    await submitPost(page);
    await saveDebugScreenshot(page, 'after-submit.png');
  } catch (error) {
    await saveDebugScreenshot(page, 'failure.png');
    throw error;
  } finally {
    await context.close();
    await browser.close();
  }

  const result = { file: options.file, status: 'posted' };
  console.log(JSON.stringify(result, null, 2));
}

main().catch(async (error) => {
  if (activePage) {
    await saveDebugScreenshot(activePage, 'fatal.png').catch(() => {});
  }
  console.error(error);
  process.exit(1);
});
