import fs from 'node:fs/promises';
import path from 'node:path';
import process from 'node:process';
import { chromium } from 'playwright';

const DEFAULT_MAX_CHARS = 500;
const DEFAULT_PROFILE_DIR = path.resolve(process.cwd(), '.playwright-meta-profile');
const DEFAULT_PROFILE_URL = 'https://www.threads.com/@your_username';
const DEFAULT_BROWSER_EXECUTABLE = '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome';
const DEFAULT_POST_RESOLUTION_TIMEOUT_MS = Number.parseInt(
  process.env.THREADS_DEBUG_POST_RESOLUTION_TIMEOUT_MS ?? '30000',
  10,
);
const DEFAULT_POST_RESOLUTION_POLL_MS = Number.parseInt(
  process.env.THREADS_DEBUG_POST_RESOLUTION_POLL_MS ?? '2000',
  10,
);
const DEFAULT_PROFILE_LINK_LIMIT = Number.parseInt(
  process.env.THREADS_DEBUG_PROFILE_LINK_LIMIT ?? '5',
  10,
);

const DEBUG_ATTEMPT_ID = process.env.THREADS_DEBUG_ATTEMPT_ID ?? null;
const DEBUG_SESSION_ID = process.env.THREADS_DEBUG_SESSION_ID ?? null;
const DEBUG_ATTEMPT_DIR = process.env.THREADS_DEBUG_ATTEMPT_DIR
  ? path.resolve(process.env.THREADS_DEBUG_ATTEMPT_DIR)
  : null;
const DEBUG_CAPTURE_SUCCESS_ARTIFACTS = /^(1|true|yes|on)$/i.test(
  process.env.THREADS_DEBUG_CAPTURE_SUCCESS_ARTIFACTS ?? 'false',
);

let activePage = null;

function parseArgs(argv) {
  const options = {
    files: [],
    maxChars: DEFAULT_MAX_CHARS,
    profileDir: DEFAULT_PROFILE_DIR,
    profileUrl: DEFAULT_PROFILE_URL,
    storageState: null,
    browserExecutable: null,
    browserChannel: null,
    headless: false,
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
      case '--storage-state':
        options.storageState = path.resolve(process.cwd(), argv[++i]);
        break;
      case '--browser-executable':
        options.browserExecutable = argv[++i];
        break;
      case '--browser-channel':
        options.browserChannel = argv[++i];
        break;
      case '--max-chars':
        options.maxChars = Number.parseInt(argv[++i], 10);
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
        options.files.push(arg);
    }
  }

  if (!options.files.length) {
    throw new Error('Usage: node scripts/threads_publish_playwright.mjs [options] <txt-file> [txt-file...]');
  }
  if (!Number.isInteger(options.maxChars) || options.maxChars <= 0) {
    throw new Error(`Invalid --max-chars value: ${options.maxChars}`);
  }

  return options;
}

function launchOptions(options) {
  const result = {
    headless: options.headless,
  };
  if (options.browserExecutable) {
    result.executablePath = options.browserExecutable;
  } else if (!options.storageState) {
    result.executablePath = DEFAULT_BROWSER_EXECUTABLE;
  }
  if (options.browserChannel) {
    result.channel = options.browserChannel;
  }
  return result;
}

function findBreakpoint(text, maxChars) {
  const window = text.slice(0, maxChars + 1);
  const preferred = ['\n- ', '\n', '. ', '! ', '? ', '; ', ': ', ', ', ' '];
  for (const token of preferred) {
    const index = window.lastIndexOf(token);
    if (index > 60) {
      return index + (token.startsWith('\n') ? 1 : token.length);
    }
  }
  return maxChars;
}

function splitChunk(chunk, maxChars) {
  const pieces = [];
  let remaining = chunk.trim();

  while (remaining) {
    if (remaining.length <= maxChars) {
      pieces.push(remaining);
      break;
    }

    const breakAt = findBreakpoint(remaining, maxChars);
    pieces.push(remaining.slice(0, breakAt).trimEnd());
    remaining = remaining.slice(breakAt).trimStart();
  }

  return pieces;
}

function splitLongPost(text, maxChars) {
  const normalized = text.replace(/\r\n/g, '\n').trim();
  if (!normalized) {
    throw new Error('Text file is empty');
  }

  const paragraphs = normalized.split('\n\n');
  const segments = [];
  let current = '';

  const push = (chunk) => {
    if (!chunk.trim()) {
      return;
    }

    const candidate = current ? `${current}\n\n${chunk}` : chunk;
    if (candidate.length <= maxChars) {
      current = candidate;
      return;
    }

    if (current) {
      segments.push(current);
      current = '';
    }

    if (chunk.length <= maxChars) {
      current = chunk;
      return;
    }

    for (const piece of splitChunk(chunk, maxChars)) {
      if (current) {
        segments.push(current);
      }
      current = piece;
    }
  };

  for (const paragraph of paragraphs) {
    push(paragraph.trim());
  }

  if (current) {
    segments.push(current);
  }

  return segments;
}

function serializeError(error) {
  return {
    message: error instanceof Error ? error.message : String(error),
    stack: error instanceof Error ? error.stack : null,
    name: error instanceof Error ? error.name : null,
  };
}

async function safeDebugOperation(fn) {
  if (!DEBUG_ATTEMPT_DIR) {
    return;
  }
  try {
    await fs.mkdir(DEBUG_ATTEMPT_DIR, { recursive: true });
    await fn();
  } catch (error) {
    console.error('Playwright debug write failed:', error);
  }
}

async function appendDebugEvent(event, payload = {}) {
  await safeDebugOperation(async () => {
    const record = {
      ts: new Date().toISOString(),
      event,
      sessionId: DEBUG_SESSION_ID,
      attemptId: DEBUG_ATTEMPT_ID,
      pid: process.pid,
      ...payload,
    };
    await fs.appendFile(
      path.join(DEBUG_ATTEMPT_DIR, 'playwright-debug.jsonl'),
      `${JSON.stringify(record)}\n`,
      'utf8',
    );
  });
}

async function writeDebugJson(filename, payload) {
  await safeDebugOperation(async () => {
    await fs.writeFile(
      path.join(DEBUG_ATTEMPT_DIR, filename),
      `${JSON.stringify(payload, null, 2)}\n`,
      'utf8',
    );
  });
}

async function writeDebugText(filename, text) {
  await safeDebugOperation(async () => {
    await fs.writeFile(path.join(DEBUG_ATTEMPT_DIR, filename), text, 'utf8');
  });
}

async function saveScreenshot(page, filename, force = false) {
  if (!force && !DEBUG_CAPTURE_SUCCESS_ARTIFACTS) {
    return;
  }
  await safeDebugOperation(async () => {
    await page.screenshot({
      path: path.join(DEBUG_ATTEMPT_DIR, filename),
      fullPage: true,
    });
  });
}

async function saveHtml(page, filename, force = false) {
  if (!force && !DEBUG_CAPTURE_SUCCESS_ARTIFACTS) {
    return;
  }
  await safeDebugOperation(async () => {
    await fs.writeFile(path.join(DEBUG_ATTEMPT_DIR, filename), await page.content(), 'utf8');
  });
}

async function captureFailureArtifacts(page, prefix) {
  await saveScreenshot(page, `${prefix}.png`, true);
  await saveHtml(page, `${prefix}.html`, true);
}

async function createContext(options) {
  const viewport = { width: 1440, height: 980 };
  if (options.storageState) {
    const browser = await chromium.launch(launchOptions(options));
    const context = await browser.newContext({
      storageState: options.storageState,
      viewport,
    });
    return {
      context,
      close: async () => {
        await context.close();
        await browser.close();
      },
      mode: 'storageState',
    };
  }

  const context = await chromium.launchPersistentContext(options.profileDir, {
    ...launchOptions(options),
    viewport,
    args: ['--start-maximized'],
  });
  return {
    context,
    close: async () => {
      await context.close();
    },
    mode: 'persistentProfile',
  };
}

function attachPageDebugListeners(page) {
  activePage = page;

  page.on('pageerror', (error) => {
    void appendDebugEvent('page_error', serializeError(error));
  });

  page.on('console', (message) => {
    if (message.type() === 'error' || message.type() === 'warning') {
      void appendDebugEvent('console_message', {
        type: message.type(),
        text: message.text(),
      });
    }
  });

  page.on('requestfailed', (request) => {
    void appendDebugEvent('request_failed', {
      url: request.url(),
      method: request.method(),
      errorText: request.failure()?.errorText ?? null,
    });
  });
}

async function collectLatestPostLinks(page, profileUrl, linkLimit = DEFAULT_PROFILE_LINK_LIMIT) {
  await page.goto(profileUrl, { waitUntil: 'domcontentloaded' });
  await page.waitForTimeout(4000);

  const rawHrefs = await page.locator('a[href*="/post/"]').evaluateAll((nodes, limit) => {
    const hrefs = [];
    for (const node of nodes) {
      const href = node.getAttribute('href');
      if (!href || hrefs.includes(href)) {
        continue;
      }
      hrefs.push(href);
      if (hrefs.length >= limit) {
        break;
      }
    }
    return hrefs;
  }, linkLimit);

  return {
    collectedAt: new Date().toISOString(),
    profileUrl,
    hrefs: rawHrefs.map((href) => new URL(href, 'https://www.threads.com').toString()),
  };
}

async function resolveNewPostUrl(page, profileUrl, baselineUrls) {
  const timeoutMs = Number.isInteger(DEFAULT_POST_RESOLUTION_TIMEOUT_MS) && DEFAULT_POST_RESOLUTION_TIMEOUT_MS > 0
    ? DEFAULT_POST_RESOLUTION_TIMEOUT_MS
    : 30000;
  const pollMs = Number.isInteger(DEFAULT_POST_RESOLUTION_POLL_MS) && DEFAULT_POST_RESOLUTION_POLL_MS > 0
    ? DEFAULT_POST_RESOLUTION_POLL_MS
    : 2000;
  const baselineSet = new Set(baselineUrls);
  const snapshots = [];
  const startedAt = Date.now();

  while (Date.now() - startedAt <= timeoutMs) {
    const snapshot = await collectLatestPostLinks(page, profileUrl);
    const newUrls = snapshot.hrefs.filter((href) => !baselineSet.has(href));
    snapshots.push({
      collectedAt: snapshot.collectedAt,
      hrefs: snapshot.hrefs,
      newUrls,
    });

    if (newUrls.length > 0) {
      const postUrl = newUrls[0];
      const resolution = snapshot.hrefs[0] === postUrl ? 'new-top-post' : 'new-post-found-in-top-list';
      return {
        postUrl,
        resolution,
        baselinePostUrls: baselineUrls,
        candidatePostUrls: snapshot.hrefs,
        newUrls,
        pollAttempts: snapshots.length,
        snapshots,
      };
    }

    if (Date.now() - startedAt + pollMs > timeoutMs) {
      break;
    }

    await page.waitForTimeout(pollMs);
  }

  const lastSnapshot = snapshots[snapshots.length - 1] ?? {
    collectedAt: new Date().toISOString(),
    hrefs: [],
  };
  return {
    postUrl: lastSnapshot.hrefs[0] ?? null,
    resolution: 'fell-back-to-current-top',
    baselinePostUrls: baselineUrls,
    candidatePostUrls: lastSnapshot.hrefs,
    newUrls: [],
    pollAttempts: snapshots.length,
    snapshots,
  };
}

async function openComposer(page, profileUrl) {
  await appendDebugEvent('open_composer_start', { profileUrl });
  await page.goto(profileUrl, { waitUntil: 'domcontentloaded' });
  await page.waitForTimeout(2000);

  const composeButton = page.locator('div[role="button"]').filter({ hasText: "What's new?" }).first();
  await composeButton.click();
  await page.waitForSelector('div[role="textbox"]', { timeout: 15000 });
  await appendDebugEvent('open_composer_ready', {
    textboxCount: await page.locator('div[role="textbox"]').count(),
  });
}

async function fillThread(page, segments) {
  const textboxes = page.locator('div[role="textbox"]');
  await appendDebugEvent('fill_thread_start', {
    segmentCount: segments.length,
    segmentLengths: segments.map((segment) => segment.length),
  });

  await textboxes.last().fill(segments[0]);

  for (const [index, segment] of segments.slice(1).entries()) {
    const countBefore = await textboxes.count();
    await page.locator('div[role="button"]').filter({ hasText: /^Add to thread$/ }).last().click();
    await page.waitForFunction(
      (expected) => document.querySelectorAll('div[role="textbox"]').length > expected,
      countBefore,
      { timeout: 10000 },
    );
    await textboxes.last().fill(segment);
    await appendDebugEvent('fill_thread_segment_added', {
      segmentIndex: index + 2,
      segmentLength: segment.length,
    });
  }

  await appendDebugEvent('fill_thread_done', {
    textboxCount: await textboxes.count(),
  });
}

async function submitThread(page) {
  await appendDebugEvent('submit_thread_start');
  await saveScreenshot(page, 'before-submit.png');

  const postButton = page.locator('div[role="button"]').filter({ hasText: /^Post$/ }).last();
  await postButton.click();

  await page.waitForFunction(
    () => !Array.from(document.querySelectorAll('div[role="button"]')).some((node) => node.textContent?.trim() === 'Cancel'),
    { timeout: 30000 },
  );
  await page.waitForTimeout(4000);
  await saveScreenshot(page, 'after-submit.png');
  await appendDebugEvent('submit_thread_done');
}

async function publishFile(page, filePath, options) {
  const absolutePath = path.resolve(process.cwd(), filePath);
  const text = await fs.readFile(absolutePath, 'utf-8');
  const segments = splitLongPost(text, options.maxChars);

  await writeDebugText('input.txt', text);
  await appendDebugEvent('publish_file_start', {
    file: absolutePath,
    maxChars: options.maxChars,
    segmentCount: segments.length,
    segmentLengths: segments.map((segment) => segment.length),
  });

  try {
    const baselineSnapshot = await collectLatestPostLinks(page, options.profileUrl);
    await writeDebugJson('profile-before.json', baselineSnapshot);

    await openComposer(page, options.profileUrl);
    await fillThread(page, segments);
    await submitThread(page);

    const resolution = await resolveNewPostUrl(page, options.profileUrl, baselineSnapshot.hrefs);
    await writeDebugJson('profile-after.json', resolution);
    await appendDebugEvent('publish_file_done', {
      file: absolutePath,
      postUrl: resolution.postUrl,
      resolution: resolution.resolution,
      pollAttempts: resolution.pollAttempts,
      candidatePostUrls: resolution.candidatePostUrls,
      newUrls: resolution.newUrls,
    });

    return {
      file: absolutePath,
      segments: segments.length,
      postUrl: resolution.postUrl,
      resolution: resolution.resolution,
      baselinePostUrls: baselineSnapshot.hrefs,
      candidatePostUrls: resolution.candidatePostUrls,
      newUrls: resolution.newUrls,
      pollAttempts: resolution.pollAttempts,
    };
  } catch (error) {
    await appendDebugEvent('publish_file_error', {
      file: absolutePath,
      ...serializeError(error),
    });
    await captureFailureArtifacts(page, 'failure');
    throw error;
  }
}

async function main() {
  const options = parseArgs(process.argv.slice(2));
  const { context, close, mode } = await createContext(options);
  const page = context.pages()[0] || await context.newPage();
  attachPageDebugListeners(page);
  const results = [];

  await appendDebugEvent('playwright_process_start', {
    mode,
    headless: options.headless,
    files: options.files.map((file) => path.resolve(process.cwd(), file)),
    profileUrl: options.profileUrl,
    usingStorageState: Boolean(options.storageState),
    usingPersistentProfile: !options.storageState,
  });

  try {
    for (const file of options.files) {
      results.push(await publishFile(page, file, options));
    }
    await writeDebugJson('results.json', { results });
  } finally {
    await appendDebugEvent('playwright_process_finish', { resultCount: results.length });
    await close();
  }

  console.log(JSON.stringify({ results }, null, 2));
}

main().catch(async (error) => {
  await appendDebugEvent('playwright_fatal_error', serializeError(error));
  if (activePage) {
    await captureFailureArtifacts(activePage, 'fatal');
  }
  console.error(error);
  process.exit(1);
});
