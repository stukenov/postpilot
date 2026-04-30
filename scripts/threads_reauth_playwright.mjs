import path from 'node:path';
import process from 'node:process';
import { chromium } from 'playwright';

const DEFAULT_BROWSER_EXECUTABLE = '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome';
const DEFAULT_SCOPES = 'threads_basic,threads_content_publish,threads_manage_insights';

function boolFromEnv(value, fallback) {
  if (value == null || value === '') {
    return fallback;
  }
  return /^(1|true|yes|on)$/i.test(value);
}

function resolveOptionalPath(value) {
  if (!value) {
    return null;
  }
  return path.resolve(process.cwd(), value);
}

function parseArgs(argv) {
  const options = {
    metaAppId: process.env.THREADS_META_APP_ID ?? '',
    threadsAppId: process.env.THREADS_APP_ID ?? '',
    redirectUri: process.env.THREADS_REDIRECT_URI ?? '',
    scopes: process.env.THREADS_SCOPES ?? DEFAULT_SCOPES,
    state: `threads-reauth-${Date.now()}`,
    storageState: resolveOptionalPath(process.env.THREADS_PLAYWRIGHT_STORAGE_STATE),
    profileDir: resolveOptionalPath(process.env.THREADS_PLAYWRIGHT_PROFILE_DIR),
    browserExecutable: process.env.THREADS_PLAYWRIGHT_BROWSER_EXECUTABLE ?? null,
    browserChannel: process.env.THREADS_PLAYWRIGHT_BROWSER_CHANNEL ?? null,
    headless: boolFromEnv(process.env.THREADS_PLAYWRIGHT_HEADLESS, true),
    screenshotPath: null,
    settingsWaitMs: 60000,
    authWaitMs: 60000,
    skipSettings: false,
  };

  for (let index = 0; index < argv.length; index += 1) {
    const arg = argv[index];
    switch (arg) {
      case '--meta-app-id':
        options.metaAppId = argv[++index];
        break;
      case '--threads-app-id':
        options.threadsAppId = argv[++index];
        break;
      case '--redirect-uri':
        options.redirectUri = argv[++index];
        break;
      case '--scopes':
        options.scopes = argv[++index];
        break;
      case '--state':
        options.state = argv[++index];
        break;
      case '--storage-state':
        options.storageState = resolveOptionalPath(argv[++index]);
        break;
      case '--profile-dir':
        options.profileDir = resolveOptionalPath(argv[++index]);
        break;
      case '--browser-executable':
        options.browserExecutable = argv[++index];
        break;
      case '--browser-channel':
        options.browserChannel = argv[++index];
        break;
      case '--screenshot-path':
        options.screenshotPath = path.resolve(process.cwd(), argv[++index]);
        break;
      case '--settings-wait-ms':
        options.settingsWaitMs = Number.parseInt(argv[++index], 10);
        break;
      case '--auth-wait-ms':
        options.authWaitMs = Number.parseInt(argv[++index], 10);
        break;
      case '--skip-settings':
        options.skipSettings = true;
        break;
      case '--headless':
        options.headless = true;
        break;
      case '--headed':
        options.headless = false;
        break;
      default:
        throw new Error(`Unknown argument: ${arg}`);
    }
  }

  if (!options.metaAppId) {
    throw new Error('Missing --meta-app-id or THREADS_META_APP_ID');
  }
  if (!options.threadsAppId) {
    throw new Error('Missing --threads-app-id or THREADS_APP_ID');
  }
  if (!options.redirectUri) {
    throw new Error('Missing --redirect-uri or THREADS_REDIRECT_URI');
  }
  if (options.storageState && options.profileDir) {
    throw new Error('Use either --storage-state or --profile-dir, not both');
  }
  if (!options.storageState && !options.profileDir) {
    throw new Error('Missing browser auth: set --storage-state or --profile-dir');
  }
  if (!Number.isInteger(options.settingsWaitMs) || options.settingsWaitMs <= 0) {
    throw new Error(`Invalid --settings-wait-ms value: ${options.settingsWaitMs}`);
  }
  if (!Number.isInteger(options.authWaitMs) || options.authWaitMs <= 0) {
    throw new Error(`Invalid --auth-wait-ms value: ${options.authWaitMs}`);
  }
  return options;
}

function buildLaunchOptions(options) {
  const launchOptions = {
    headless: options.headless,
  };
  if (options.browserExecutable) {
    launchOptions.executablePath = options.browserExecutable;
  } else if (!options.storageState) {
    launchOptions.executablePath = DEFAULT_BROWSER_EXECUTABLE;
  }
  if (options.browserChannel) {
    launchOptions.channel = options.browserChannel;
  }
  return launchOptions;
}

function buildSettingsUrl(metaAppId) {
  return `https://developers.facebook.com/apps/${metaAppId}/use_cases/customize/?use_case_enum=THREADS_API&selected_tab=settings&product_route=threads-api`;
}

function buildAuthorizeUrl(options) {
  const params = new URLSearchParams({
    client_id: options.threadsAppId,
    redirect_uri: options.redirectUri,
    scope: options.scopes,
    response_type: 'code',
    state: options.state,
  });
  return `https://threads.net/oauth/authorize?${params.toString()}`;
}

function sanitizeUrl(value) {
  if (!value) {
    return value;
  }
  const parsed = new URL(value);
  for (const key of ['code', 'access_token']) {
    if (parsed.searchParams.has(key)) {
      parsed.searchParams.set(key, '[redacted]');
    }
  }
  return parsed.toString();
}

async function safeBodyText(page) {
  return ((await page.locator('body').innerText().catch(() => '')).replace(/\s+/g, ' ').trim());
}

async function waitForUseCaseSettings(page, options) {
  const start = Date.now();
  let reloaded = false;

  while (Date.now() - start < options.settingsWaitMs) {
    const body = await safeBodyText(page);
    if (
      body.includes('URL обратного вызова для перенаправления')
      || body.includes('ID приложения Threads')
      || body.includes(options.redirectUri)
    ) {
      return body;
    }
    if (!reloaded && Date.now() - start > options.settingsWaitMs / 2) {
      await page.reload({ waitUntil: 'domcontentloaded', timeout: 60000 }).catch(() => {});
      reloaded = true;
    }
    await page.waitForTimeout(1500);
  }

  throw new Error(`Timed out waiting for Threads API use case settings page after ${options.settingsWaitMs}ms`);
}

async function findRedirectInput(page) {
  const locator = page.locator('input[type="text"]');
  const count = await locator.count();
  for (let index = 0; index < count; index += 1) {
    const input = locator.nth(index);
    if (!(await input.isVisible().catch(() => false))) {
      continue;
    }
    const placeholder = (await input.getAttribute('placeholder').catch(() => '')) ?? '';
    if (/поиск/i.test(placeholder)) {
      continue;
    }
    const value = await input.inputValue().catch(() => '');
    if (!value.trim()) {
      return input;
    }
  }
  throw new Error('Could not find the redirect URI input in Meta App settings');
}

async function clickExactText(page, label) {
  const buttons = page.locator('[role="button"],button');
  const count = await buttons.count();
  for (let index = 0; index < count; index += 1) {
    const button = buttons.nth(index);
    if (!(await button.isVisible().catch(() => false))) {
      continue;
    }
    const text = ((await button.innerText().catch(() => '')).replace(/\s+/g, ' ').trim());
    if (text === label) {
      await button.click();
      return true;
    }
  }
  return false;
}

async function ensureRedirectUri(page, options) {
    const settingsUrl = buildSettingsUrl(options.metaAppId);
  if (options.skipSettings) {
    return { settingsUrl, redirectUriAdded: false, skipped: true };
  }
  await page.goto(settingsUrl, { waitUntil: 'domcontentloaded', timeout: 60000 });
  const body = await waitForUseCaseSettings(page, options);
  if (body.includes(options.redirectUri)) {
    return { settingsUrl, redirectUriAdded: false };
  }

  const redirectInput = await findRedirectInput(page);
  await redirectInput.fill(options.redirectUri);
  await redirectInput.press('Enter');
  if (!(await clickExactText(page, 'Сохранить')) && !(await clickExactText(page, 'Save'))) {
    throw new Error('Could not find the Save button in Meta App settings');
  }
  await page.waitForTimeout(3000);
  await page.goto(settingsUrl, { waitUntil: 'domcontentloaded', timeout: 60000 });
  const afterReload = await waitForUseCaseSettings(page, options);
  if (!afterReload.includes(options.redirectUri)) {
    throw new Error(`Redirect URI was not persisted in Meta App settings: ${options.redirectUri}`);
  }
  return { settingsUrl, redirectUriAdded: true };
}

async function maybeClickConsent(page) {
  const candidates = [
    page.getByRole('button', { name: /Continue as|Продолжить как|Continue|Allow|Разрешить|OK|Open/i }),
    page.getByRole('link', { name: /Continue as|Продолжить как|Continue|Allow|Разрешить|OK|Open/i }),
    page.locator('button'),
    page.locator('a[role="button"]'),
    page.locator('input[type="submit"]'),
  ];

  for (const locator of candidates) {
    const count = await locator.count().catch(() => 0);
    for (let index = 0; index < Math.min(count, 5); index += 1) {
      const item = locator.nth(index);
      if (!(await item.isVisible().catch(() => false))) {
        continue;
      }
      const text = (
        (await item.innerText().catch(() => ''))
        || (await item.getAttribute('value').catch(() => ''))
        || ''
      ).replace(/\s+/g, ' ').trim();
      if (!text) {
        continue;
      }
      if (!/Continue as|Продолжить как|Continue|Allow|Разрешить|OK|Open/i.test(text)) {
        continue;
      }
      await item.click({ timeout: 2000 }).catch(() => {});
      await page.waitForTimeout(1500);
      return text;
    }
  }
  return null;
}

function parseOauthError(url, body) {
  if (!/oauth\/authorize\/error\.json/.test(url) && !body.startsWith('{')) {
    return null;
  }
  try {
    const payload = JSON.parse(body);
    return payload.error_message || JSON.stringify(payload);
  } catch {
    return body;
  }
}

async function runAuthorizeFlow(page, options) {
  const authorizeUrl = buildAuthorizeUrl(options);
  const start = Date.now();
  const redirectPrefix = options.redirectUri;
  const events = [];

  await page.goto(authorizeUrl, { waitUntil: 'domcontentloaded', timeout: 60000 });

  while (Date.now() - start < options.authWaitMs) {
    const currentUrl = page.url();
    const body = await safeBodyText(page);
    events.push({
      url: sanitizeUrl(currentUrl),
      title: await page.title().catch(() => ''),
      body: body.slice(0, 500),
    });

    if (currentUrl.startsWith(redirectPrefix)) {
      return {
        authorizeUrl,
        finalUrl: sanitizeUrl(currentUrl),
        consentClicks: events.filter((event) => event.clickedLabel).length,
        events,
      };
    }

    const oauthError = parseOauthError(currentUrl, body);
    if (oauthError) {
      throw new Error(`OAuth authorize failed: ${oauthError}`);
    }

    const clickedLabel = await maybeClickConsent(page);
    if (clickedLabel) {
      events[events.length - 1].clickedLabel = clickedLabel;
      continue;
    }

    await page.waitForTimeout(1500);
  }

  throw new Error(`Timed out waiting for redirect to ${redirectPrefix}`);
}

async function createBrowserContext(options) {
  const launchOptions = buildLaunchOptions(options);
  if (options.profileDir) {
    const context = await chromium.launchPersistentContext(options.profileDir, {
      ...launchOptions,
      viewport: { width: 1440, height: 1200 },
    });
    return {
      browser: null,
      context,
      close: async () => context.close(),
      persist: async () => {},
    };
  }

  const browser = await chromium.launch(launchOptions);
  const context = await browser.newContext({
    storageState: options.storageState,
    viewport: { width: 1440, height: 1200 },
  });
  return {
    browser,
    context,
    close: async () => {
      await context.close();
      await browser.close();
    },
    persist: async () => {
      if (options.storageState) {
        await context.storageState({ path: options.storageState });
      }
    },
  };
}

async function main() {
  const options = parseArgs(process.argv.slice(2));
  const runtime = await createBrowserContext(options);
  const page = runtime.context.pages()[0] || await runtime.context.newPage();

  try {
    const settingsResult = await ensureRedirectUri(page, options);
    const authResult = await runAuthorizeFlow(page, options);
    if (options.screenshotPath) {
      await page.screenshot({ path: options.screenshotPath, fullPage: true }).catch(() => {});
    }
    await runtime.persist();
    console.log(JSON.stringify({
      metaAppId: options.metaAppId,
      threadsAppId: options.threadsAppId,
      redirectUri: options.redirectUri,
      scopes: options.scopes,
      state: options.state,
      settings: settingsResult,
      auth: authResult,
    }, null, 2));
  } finally {
    await runtime.close();
  }
}

main().catch((error) => {
  console.error(error);
  process.exit(1);
});
