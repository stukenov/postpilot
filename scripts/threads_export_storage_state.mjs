import fs from 'node:fs/promises';
import path from 'node:path';
import process from 'node:process';
import { chromium } from 'playwright';

const DEFAULT_PROFILE_DIR = path.resolve(process.cwd(), '.playwright-meta-profile');
const DEFAULT_PROFILE_URL = 'https://www.threads.com/@your_username';
const DEFAULT_BROWSER_EXECUTABLE = '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome';

function parseArgs(argv) {
  const options = {
    outputPath: null,
    profileDir: DEFAULT_PROFILE_DIR,
    profileUrl: DEFAULT_PROFILE_URL,
    browserExecutable: DEFAULT_BROWSER_EXECUTABLE,
  };

  for (let i = 0; i < argv.length; i += 1) {
    const arg = argv[i];
    switch (arg) {
      case '--profile-dir':
        options.profileDir = path.resolve(process.cwd(), argv[++i]);
        break;
      case '--profile-url':
        options.profileUrl = argv[++i];
        break;
      case '--browser-executable':
        options.browserExecutable = argv[++i];
        break;
      default:
        if (arg.startsWith('--')) {
          throw new Error(`Unknown argument: ${arg}`);
        }
        if (options.outputPath) {
          throw new Error('Only one output path is allowed');
        }
        options.outputPath = path.resolve(process.cwd(), arg);
    }
  }

  if (!options.outputPath) {
    throw new Error('Usage: node scripts/threads_export_storage_state.mjs [options] <output-path>');
  }
  return options;
}

async function main() {
  const options = parseArgs(process.argv.slice(2));
  await fs.mkdir(path.dirname(options.outputPath), { recursive: true });

  const context = await chromium.launchPersistentContext(options.profileDir, {
    executablePath: options.browserExecutable,
    headless: false,
    viewport: { width: 1440, height: 980 },
    args: ['--start-maximized'],
  });

  try {
    const page = context.pages()[0] || await context.newPage();
    await page.goto(options.profileUrl, { waitUntil: 'domcontentloaded' });
    await page.waitForTimeout(3000);
    await context.storageState({ path: options.outputPath });
  } finally {
    await context.close();
  }

  console.log(JSON.stringify({ storageState: options.outputPath }, null, 2));
}

main().catch((error) => {
  console.error(error);
  process.exit(1);
});
