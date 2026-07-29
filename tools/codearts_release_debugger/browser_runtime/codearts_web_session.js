const fs = require('fs');
const os = require('os');
const path = require('path');
const crypto = require('crypto');
const { chromium } = require('playwright');
const loginDiagnosticHistory = [];
let lastDownloadDiagnostic = null;

function writeProgress(config, stage, message, extra = {}) {
  if (!config.progressPath) return;
  try {
    fs.writeFileSync(config.progressPath, JSON.stringify({ stage, message, ...extra }), 'utf8');
  } catch (_) {}
}

function findBrowser() {
  let playwrightChromium = '';
  try {
    playwrightChromium = chromium.executablePath();
  } catch (_) {}
  const candidates = [
    { name: 'configured browser', path: process.env.PCIDS_BROWSER_EXECUTABLE },
    {
      name: 'Google Chrome',
      path: process.env.PROGRAMFILES &&
        path.join(process.env.PROGRAMFILES, 'Google', 'Chrome', 'Application', 'chrome.exe'),
    },
    {
      name: 'Google Chrome',
      path: process.env['PROGRAMFILES(X86)'] &&
        path.join(process.env['PROGRAMFILES(X86)'], 'Google', 'Chrome', 'Application', 'chrome.exe'),
    },
    {
      name: 'Google Chrome',
      path: process.env.LOCALAPPDATA &&
        path.join(process.env.LOCALAPPDATA, 'Google', 'Chrome', 'Application', 'chrome.exe'),
    },
    {
      name: 'Microsoft Edge',
      path: process.env['PROGRAMFILES(X86)'] &&
        path.join(process.env['PROGRAMFILES(X86)'], 'Microsoft', 'Edge', 'Application', 'msedge.exe'),
    },
    {
      name: 'Microsoft Edge',
      path: process.env.PROGRAMFILES &&
        path.join(process.env.PROGRAMFILES, 'Microsoft', 'Edge', 'Application', 'msedge.exe'),
    },
    {
      name: 'Microsoft Edge',
      path: process.env.LOCALAPPDATA &&
        path.join(process.env.LOCALAPPDATA, 'Microsoft', 'Edge', 'Application', 'msedge.exe'),
    },
    { name: 'bundled Playwright Chromium', path: playwrightChromium },
  ];
  return candidates.find(candidate => candidate.path && fs.existsSync(candidate.path));
}

function valueFingerprint(value) {
  if (!value) return '';
  return crypto.createHash('sha256').update(String(value)).digest('hex').slice(0, 12);
}

function buildRequestDebug(headers, transport, url, payload) {
  const normalized = Object.fromEntries(
    Object.entries(headers || {}).map(([name, value]) => [String(name).toLowerCase(), String(value)]),
  );
  const cookieHeader = normalized.cookie || '';
  const cookieNames = cookieHeader
    .split(';')
    .map(part => part.split('=')[0].trim())
    .filter(Boolean);
  const csrfValue = normalized.cftk || normalized['x-csrf-token'] || normalized['x-xsrf-token'] || '';
  const bodyText = payload === undefined ? '' : JSON.stringify(payload);
  let targetHost = '';
  try { targetHost = new URL(url).host; } catch (_) {}
  return {
    transport,
    target_host: targetHost,
    header_names: Object.keys(normalized).sort(),
    session_cookie_names: cookieNames,
    session_cookie_count: cookieNames.length,
    csrf_header_present: Boolean(csrfValue),
    csrf_header_length: csrfValue.length,
    csrf_header_fingerprint: valueFingerprint(csrfValue),
    origin: normalized.origin || '',
    referer: normalized.referer || '',
    content_type: normalized['content-type'] || '',
    request_body_bytes: Buffer.byteLength(bodyText, 'utf8'),
  };
}

function safeResponseHeaders(headers) {
  const source = headers || {};
  const names = [
    'content-type', 'content-length', 'content-disposition', 'location',
    'server', 'x-request-id', 'x-trace-id', 'trace-id',
  ];
  return names.reduce((result, name) => {
    if (source[name]) result[name] = source[name];
    return result;
  }, {});
}

function bodyDiagnostics(text, contentType) {
  const isHtml = String(contentType || '').toLowerCase().includes('text/html') || /^\s*<!doctype html/i.test(text || '');
  const titleMatch = isHtml ? String(text || '').match(/<title[^>]*>([^<]*)<\/title>/i) : null;
  return {
    response_body_kind: isHtml ? 'html' : 'json_or_text',
    response_body_bytes: Buffer.byteLength(String(text || ''), 'utf8'),
    html_title: titleMatch ? titleMatch[1].trim() : '',
  };
}

async function fillFirst(scope, selectors, value) {
  if (!value) return false;
  for (const selector of selectors) {
    try {
      const targets = scope.locator(selector);
      const count = await targets.count();
      for (let index = 0; index < count; index += 1) {
        const target = targets.nth(index);
        if (await target.isVisible({ timeout: 300 }) && await target.isEnabled({ timeout: 300 })) {
          await target.fill(value);
          return true;
        }
      }
    } catch (_) {}
  }
  return false;
}

async function fillByLabel(scope, labels, value) {
  if (!value) return false;
  for (const label of labels) {
    try {
      const targets = scope.getByLabel(label);
      const count = await targets.count();
      for (let index = 0; index < count; index += 1) {
        const target = targets.nth(index);
        if (await target.isVisible({ timeout: 300 }) && await target.isEnabled({ timeout: 300 })) {
          await target.fill(value);
          return true;
        }
      }
    } catch (_) {}
  }
  return false;
}

async function clickFirst(scope, selectors) {
  for (const selector of selectors) {
    try {
      const targets = scope.locator(selector);
      const count = await targets.count();
      for (let index = 0; index < count; index += 1) {
        const target = targets.nth(index);
        if (await target.isVisible({ timeout: 300 }) && await target.isEnabled({ timeout: 300 })) {
          await target.click();
          return true;
        }
      }
    } catch (_) {}
  }
  return false;
}

async function fillUsernameFallback(scope, value, domainValue) {
  if (!value) return false;
  try {
    const candidates = scope.locator(
      'input:not([type="password"]):not([type="hidden"]):not([type="checkbox"]):not([type="radio"])',
    );
    const count = await candidates.count();
    for (let index = 0; index < count; index += 1) {
      const target = candidates.nth(index);
      if (!await target.isVisible({ timeout: 200 }) || !await target.isEnabled({ timeout: 200 })) continue;
      const attributes = [
        await target.getAttribute('name'),
        await target.getAttribute('id'),
        await target.getAttribute('placeholder'),
        await target.getAttribute('aria-label'),
        await target.getAttribute('autocomplete'),
      ].filter(Boolean).join(' ').toLowerCase();
      if (/captcha|verify|verification|mobile|phone|search|租户|账号名|账户名|domain|tenant/.test(attributes)) {
        continue;
      }
      const currentValue = await target.inputValue().catch(() => '');
      if (currentValue && currentValue === String(domainValue || '')) continue;
      if (currentValue && currentValue !== String(value)) continue;
      await target.fill(value);
      return true;
    }
  } catch (_) {}
  return false;
}

async function tryAutomaticLogin(page, config) {
  const iamModeSelectors = [
    '[role="tab"]:has-text("IAM用户登录")', '[role="tab"]:has-text("IAM 用户登录")',
    'button:has-text("IAM用户登录")', 'button:has-text("IAM 用户登录")',
    'a:has-text("IAM用户登录")', 'a:has-text("IAM 用户登录")',
    '[role="tab"]:has-text("IAM用户")', 'button:has-text("IAM用户")', 'a:has-text("IAM用户")',
  ];
  const domainSelectors = [
    'input[name="domain"]', 'input[name="domainName"]', 'input[name="tenant"]',
    'input[name="accountName"]', 'input[name="tenantName"]',
    '#domain', '#domainName', '#tenantName', '#accountName', 'input[placeholder*="租户"]',
    'input[placeholder*="账号名"]', 'input[placeholder*="帐户名"]',
    'input[placeholder*="账户名"]', 'input[aria-label*="租户"]', 'input[aria-label*="账号名"]',
  ];
  const usernameSelectors = [
    'input[name="username"]', 'input[name="userName"]', 'input[name="userAccount"]',
    'input[name="account"]', 'input[name="iamUsername"]', 'input[name="iamUserName"]',
    'input[name="subUserName"]', 'input[name="loginName"]',
    '#username', '#userName', '#userAccount', '#account', '#iamUsername', '#iamUserName',
    '#subUserName', '#loginName', 'input[placeholder*="IAM用户名"]',
    'input[placeholder*="IAM 用户名"]', 'input[placeholder*="用户名"]',
    'input[placeholder*="登录名"]', 'input[placeholder*="用户账号"]',
    'input[aria-label*="IAM用户名"]', 'input[aria-label*="用户名"]',
  ];
  const passwordSelectors = [
    'input[name="password"]', 'input[name="userPassword"]', '#password', '#userPassword',
    'input[placeholder*="密码"]', 'input[type="password"]',
  ];
  const nextSelectors = [
    'button:has-text("下一步")', 'button:has-text("继续")',
    'input[type="submit"]', 'button[type="submit"]',
  ];
  const loginSelectors = [
    'button:has-text("登录")', 'button:has-text("登 录")', 'input[type="submit"]',
    '#loginBtn', '.login-btn', 'button[type="submit"]',
  ];

  const diagnostic = {
    iamModeSelected: false,
    domainFilled: false,
    usernameFilled: false,
    passwordFilled: false,
    nextClicked: false,
    submitted: false,
    frameCount: page.frames().length,
  };
  for (const scope of page.frames()) {
    if (await clickFirst(scope, iamModeSelectors)) diagnostic.iamModeSelected = true;
  }
  if (diagnostic.iamModeSelected) await page.waitForTimeout(500);

  const fillKnownFields = async () => {
    for (const scope of page.frames()) {
      if (!diagnostic.domainFilled) {
        diagnostic.domainFilled =
          await fillFirst(scope, domainSelectors, config.domain) ||
          await fillByLabel(scope, [/租户/, /账号名/, /账户名/, /Domain/i], config.domain);
      }
      if (!diagnostic.usernameFilled) {
        diagnostic.usernameFilled =
          await fillFirst(scope, usernameSelectors, config.username) ||
          await fillByLabel(scope, [/IAM\s*用户名/i, /用户名/, /登录名/], config.username);
      }
    }
  };

  await fillKnownFields();
  if (!diagnostic.usernameFilled && diagnostic.domainFilled) {
    for (const scope of page.frames()) {
      if (await clickFirst(scope, nextSelectors)) {
        diagnostic.nextClicked = true;
        break;
      }
    }
    if (diagnostic.nextClicked) {
      await page.waitForTimeout(1200);
      await fillKnownFields();
    }
  }
  if (!diagnostic.usernameFilled) {
    for (const scope of page.frames()) {
      if (await fillUsernameFallback(scope, config.username, config.domain)) {
        diagnostic.usernameFilled = true;
        break;
      }
    }
  }
  for (const scope of page.frames()) {
    if (
      await fillFirst(scope, passwordSelectors, config.password) ||
      await fillByLabel(scope, [/IAM\s*密码/i, /密码/], config.password)
    ) {
      diagnostic.passwordFilled = true;
      break;
    }
  }
  if (!diagnostic.passwordFilled && diagnostic.usernameFilled && !diagnostic.nextClicked) {
    for (const scope of page.frames()) {
      if (await clickFirst(scope, nextSelectors)) {
        diagnostic.nextClicked = true;
        break;
      }
    }
    if (diagnostic.nextClicked) {
      await page.waitForTimeout(1200);
      await fillKnownFields();
      for (const scope of page.frames()) {
        if (
          await fillFirst(scope, passwordSelectors, config.password) ||
          await fillByLabel(scope, [/IAM\s*密码/i, /密码/], config.password)
        ) {
          diagnostic.passwordFilled = true;
          break;
        }
      }
    }
  }
  if (diagnostic.usernameFilled && diagnostic.passwordFilled) {
    for (const submitScope of page.frames()) {
      if (await clickFirst(submitScope, loginSelectors)) {
        diagnostic.submitted = true;
        break;
      }
    }
  }
  return diagnostic;
}

async function main() {
  const configPath = process.argv[2];
  const resultPath = process.argv[3];
  const config = JSON.parse(fs.readFileSync(configPath, 'utf8'));
  const browser = findBrowser();
  if (!browser) {
    throw new Error('未找到可用浏览器；请部署 Playwright Chromium，或启用系统 Microsoft Edge/Google Chrome');
  }

  const cacheRoot = path.join(process.env.LOCALAPPDATA || os.tmpdir(), 'CodeArtsWebFilesList');
  const profile = path.join(cacheRoot, 'browser-profile');
  fs.mkdirSync(profile, { recursive: true });
  const started = Date.now();
  const context = await chromium.launchPersistentContext(profile, {
    executablePath: browser.path,
    headless: false,
    ignoreHTTPSErrors: true,
    viewport: null,
    args: ['--start-maximized', '--disable-features=TranslateUI'],
  });
  let capturedCftk = '';
  let capturedApiHeaders = {};
  let capturedListTemplate = null;
  context.on('request', async request => {
    try {
      if (!request.url().includes('/cloudartifact/')) return;
      const headers = await request.allHeaders();
      capturedCftk = headers.cftk || headers.Cftk || capturedCftk;
      const allowedHeaders = [
        'cftk', 'language', 'x-language', 'x-requested-with',
        'x-auth-token', 'x-csrf-token', 'x-xsrf-token',
      ];
      capturedApiHeaders = allowedHeaders.reduce((result, name) => {
        if (headers[name]) result[name] = headers[name];
        return result;
      }, capturedApiHeaders);
    } catch (_) {}
  });
  context.on('response', async response => {
    try {
      const request = response.request();
      const responseUrl = response.url();
      if (request.method() !== 'POST' || !responseUrl.includes('/cloudartifact/v1/files/list')) return;
      const contentType = String(response.headers()['content-type'] || '').toLowerCase();
      if (!response.ok() || !contentType.includes('json')) return;
      const body = await response.json();
      if (!body || !body.result || !Array.isArray(body.result.data)) return;
      const headers = await request.allHeaders();
      let payload = {};
      try { payload = request.postDataJSON() || {}; } catch (_) {}
      if (String(payload.projectId || '') !== String(config.projectId || '')) return;
      if (body.result.data.some(item => String(item && item.type || '').toLowerCase() === 'project')) return;
      capturedCftk = headers.cftk || headers.Cftk || capturedCftk;
      const allowedHeaders = [
        'accept', 'content-type', 'cftk', 'language', 'x-language', 'x-requested-with',
        'x-auth-token', 'x-csrf-token', 'x-xsrf-token',
      ];
      capturedApiHeaders = allowedHeaders.reduce((result, name) => {
        if (headers[name]) result[name] = headers[name];
        return result;
      }, {});
      capturedListTemplate = {
        url: responseUrl,
        payload,
        headers: capturedApiHeaders,
        rawHeaders: headers,
        response: {
          status: response.status(),
          ok: response.ok(),
          redirected: false,
          content_type: contentType,
          headers: safeResponseHeaders(response.headers()),
          url: responseUrl,
          elapsed_ms: 0,
          body,
          request_debug: {
            ...buildRequestDebug(headers, 'browser_native_captured_response', responseUrl, payload),
            ...bodyDiagnostics(JSON.stringify(body), contentType),
          },
        },
      };
    } catch (_) {}
  });

  try {
    const page = context.pages()[0] || await context.newPage();
    writeProgress(config, 'browser', `${browser.name} 已打开，正在检查缓存登录状态`);
    await page.goto(config.repositoryUrl, { waitUntil: 'domcontentloaded', timeout: 0 });
    let automaticLoginAttempts = 0;
    let automaticLoginSubmittedAt = 0;
    let manualProgressWritten = false;
    while (!page.url().startsWith(config.repositoryPrefix)) {
      if (automaticLoginAttempts < 5 && !automaticLoginSubmittedAt) {
        automaticLoginAttempts += 1;
        const loginDiagnostic = await tryAutomaticLogin(page, config);
        loginDiagnostic.attempt = automaticLoginAttempts;
        loginDiagnosticHistory.push(loginDiagnostic);
        console.log(`PCIDS_WEB_AUTOFILL ${JSON.stringify(loginDiagnostic)}`);
        const submitted = loginDiagnostic.submitted;
        if (submitted) automaticLoginSubmittedAt = Date.now();
        writeProgress(
          config,
          submitted ? 'login' : 'browser',
          submitted ? '已自动填写账号密码，正在等待登录结果' : `正在等待登录页面，准备第 ${automaticLoginAttempts}/5 次自动填写`,
          { autofill: loginDiagnostic },
        );
      } else if (!manualProgressWritten && (
        automaticLoginAttempts >= 5 ||
        (automaticLoginSubmittedAt && Date.now() - automaticLoginSubmittedAt > 8000)
      )) {
        manualProgressWritten = true;
        writeProgress(config, 'manual', '自动登录未进入制品仓库，请在 Chrome 中手动完成登录和页面跳转');
      }
      await page.waitForTimeout(1000);
    }

    await page.waitForLoadState('domcontentloaded', { timeout: 30000 }).catch(() => {});
    await page.waitForTimeout(2500);
    for (let attempt = 0; attempt < 20 && !capturedListTemplate; attempt += 1) {
      writeProgress(config, 'session', '已进入制品仓库，正在捕获页面实际的文件列表请求');
      await page.waitForTimeout(500);
    }
    const cookies = await context.cookies();
    const cftkCookie = cookies.find(cookie => cookie.name.toLowerCase() === 'cftk') ||
      cookies.find(cookie => cookie.name.toLowerCase().endsWith('cftk'));
    const cftk = capturedCftk || (cftkCookie && cftkCookie.value) || '';
    if (!cftk) throw new Error('已进入制品仓库，但没有获取到 cftk；请刷新仓库页面后重新点击拉取');
    if (!capturedListTemplate) {
      throw new Error('页面已打开，但没有捕获到成功的 files/list 请求；请确认页面已显示文件后重新点击按钮');
    }
    writeProgress(config, 'session', '已捕获页面实际 files/list 请求，Cookie、cftk 和请求参数获取成功');

    const capturedListUrl = capturedListTemplate.url;
    const capturedApiOrigin = new URL(capturedListUrl).origin;
    const capturedPayload = {
      ...config.payload,
      ...capturedListTemplate.payload,
      projectId: config.projectId,
    };
    const templateCftk = capturedListTemplate.headers.cftk || capturedCftk || cftk;

    const requestJson = async (method, url, payload) => {
      const headers = {
        ...(capturedListTemplate.rawHeaders || capturedListTemplate.headers || capturedApiHeaders),
        cftk: templateCftk,
      };
      delete headers['content-length'];
      delete headers.host;
      const options = {
        method,
        headers,
        timeout: 0,
        failOnStatusCode: false,
      };
      if (payload !== undefined) options.data = JSON.stringify(payload);
      const startedAt = Date.now();
      const response = await context.request.fetch(url, options);
      const text = await response.text();
      let body;
      try { body = text.trim() ? JSON.parse(text) : null; }
      catch (_) { body = { _non_json_body_preview: text.slice(0, 2000) }; }
      const responseHeaders = response.headers();
      const contentType = responseHeaders['content-type'] || '';
      return {
        status: response.status(),
        ok: response.ok(),
        redirected: response.url() !== url,
        content_type: contentType,
        headers: safeResponseHeaders(responseHeaders),
        url: response.url(),
        elapsed_ms: Date.now() - startedAt,
        body,
        request_debug: {
          ...buildRequestDebug(headers, 'playwright_api_request_replay', url, payload),
          ...bodyDiagnostics(text, contentType),
          requested_url: url,
          final_url: response.url(),
          redirected: response.url() !== url,
        },
      };
    };

    // Download mode reuses the authenticated browser request captured above.
    // Web-only artifact URLs cannot be fetched by the backend with IAM/basic
    // credentials, so write the raw response to the caller-provided temporary
    // path and let PCIDS encrypt it through the normal repository pipeline.
    if (config.downloadUrl && config.downloadOutputPath) {
      writeProgress(config, 'download', '登录会话已就绪，正在下载制品');
      const headers = {
        ...(capturedListTemplate.rawHeaders || capturedListTemplate.headers || capturedApiHeaders),
        cftk: templateCftk,
        referer: page.url(),
        origin: new URL(page.url()).origin,
      };
      delete headers['content-length'];
      delete headers.host;
      const startedAt = Date.now();
      const downloadResponse = await context.request.fetch(config.downloadUrl, {
        method: 'GET',
        headers,
        timeout: 0,
        failOnStatusCode: false,
      });
      const bytes = await downloadResponse.body();
      const responseHeaders = downloadResponse.headers();
      const contentType = responseHeaders['content-type'] || '';
      const textPreview = bytes.subarray(0, 4096).toString('utf8');
      const debug = {
        ...buildRequestDebug(headers, 'playwright_api_request_replay', config.downloadUrl, undefined),
        ...bodyDiagnostics(textPreview, contentType),
        requested_url: config.downloadUrl,
        final_url: downloadResponse.url(),
        redirected: downloadResponse.url() !== config.downloadUrl,
      };
      lastDownloadDiagnostic = {
        interface: 'GET webpage download',
        method: 'GET',
        url: config.downloadUrl,
        payload: { _request_debug: debug },
        response: {
          status: downloadResponse.status(),
          headers: safeResponseHeaders(responseHeaders),
          request_debug: debug,
          elapsed_ms: Date.now() - startedAt,
          received_bytes: bytes.length,
        },
      };
      const failed = [401, 403].includes(downloadResponse.status()) ||
        String(contentType).toLowerCase().includes('text/html') ||
        /<html|<!doctype/i.test(textPreview);
      if (failed) {
        throw new Error(`网页下载会话失效或被重定向：HTTP ${downloadResponse.status()} ${debug.html_title || ''}`);
      }
      if (!downloadResponse.ok()) {
        throw new Error(`网页下载失败：HTTP ${downloadResponse.status()}`);
      }
      if (!bytes.length) {
        throw new Error('网页下载失败：服务器返回了空文件');
      }
      fs.writeFileSync(config.downloadOutputPath, bytes);
      writeProgress(config, 'done', `制品下载完成：${bytes.length} 字节`, {
        downloadedBytes: bytes.length,
        httpStatus: downloadResponse.status(),
      });
      fs.writeFileSync(resultPath, JSON.stringify({
        response: {
          ok: true,
          status: downloadResponse.status(),
          body: { result: { downloaded: true } },
        },
        requestRecords: [lastDownloadDiagnostic],
        loginDiagnostics: loginDiagnosticHistory,
      }), 'utf8');
      return;
    }

    const entries = [];
    const requestRecords = [];
    const directoryErrors = [];
    const detailErrors = [];
    const visitedFolders = new Set();
    let folderCount = 0;
    let fileCount = 0;
    let detailSuccessCount = 0;
    let sessionExpired = false;
    let sessionFailureResponse = null;
    let capturedRootResponseConsumed = false;

    const recordRequest = (interfaceName, method, url, payload, response) => {
      requestRecords.push({
        interface: interfaceName,
        method,
        url,
        payload: { ...payload, _request_debug: response.request_debug || {} },
        response,
      });
    };
    const isFolder = item => ['folder', 'directory', 'dir'].includes(String(item && item.type || '').toLowerCase());
    const isSessionFailure = response => {
      const contentType = String(response && response.content_type || '').toLowerCase();
      const finalUrl = String(response && response.url || '').toLowerCase();
      return Boolean(
        response && [401, 403].includes(Number(response.status)) ||
        contentType.includes('text/html') ||
        finalUrl.includes('/login') || finalUrl.includes('/auth/realms/')
      );
    };

    const fetchDetail = async item => {
      const itemId = String(item.id || '').trim();
      if (!itemId) {
        const error = { name: item.name, message: '文件列表项缺少 id，无法调用详情接口' };
        detailErrors.push(error);
        entries.push({ ...item, _detail_error: error });
        return;
      }
      writeProgress(config, 'detail', `正在获取文件详情：${item.name || itemId}`, {
        folderCount, fileCount, detailCurrent: detailSuccessCount + detailErrors.length + 1,
      });
      const detailUrl = `${capturedApiOrigin}/cloudartifact/v1/files/${encodeURIComponent(itemId)}/info?_=${Date.now()}`;
      const response = await requestJson('GET', detailUrl);
      recordRequest('GET /cloudartifact/v1/files/{id}/info', 'GET', detailUrl, { id: itemId }, response);
      if (isSessionFailure(response)) {
        sessionExpired = true;
        sessionFailureResponse = response;
        detailErrors.push({ id: itemId, name: item.name, status: response.status, message: 'Cookie 或 cftk 已失效' });
        entries.push({ ...item, _detail_error: detailErrors[detailErrors.length - 1] });
        return;
      }
      const bodyResult = response.body && response.body.result;
      const apiSucceeded = response.ok && response.body && response.body.status !== 'error' && bodyResult && typeof bodyResult === 'object';
      if (apiSucceeded) {
        detailSuccessCount += 1;
        entries.push({ ...item, ...bodyResult, id: item.id, _list: item, _detail: bodyResult });
      } else {
        const error = { id: itemId, name: item.name, status: response.status, body: response.body };
        detailErrors.push(error);
        entries.push({ ...item, _detail_error: error });
      }
    };

    const fetchDirectory = async (parentId, directoryPath) => {
      const visitKey = parentId || '__root__';
      if (visitedFolders.has(visitKey) || sessionExpired) return;
      visitedFolders.add(visitKey);
      let pageNo = Math.max(1, Number(config.payload.pageNo) || 1);
      let totalPages = pageNo;
      do {
        const payload = { ...capturedPayload, pageNo };
        if (parentId) payload.parentId = parentId;
        writeProgress(config, 'directory', `正在读取目录：${directoryPath || '/'}`, {
          folderCount, fileCount, visitedDirectoryCount: visitedFolders.size,
        });
        const listUrl = `${capturedListUrl.split('?')[0]}?_=${Date.now()}`;
        let response;
        let effectiveListUrl = listUrl;
        const capturedPageNo = Math.max(1, Number(capturedListTemplate.payload.pageNo) || 1);
        if (!parentId && !capturedRootResponseConsumed && pageNo === capturedPageNo) {
          response = capturedListTemplate.response;
          effectiveListUrl = capturedListUrl;
          capturedRootResponseConsumed = true;
        } else {
          response = await requestJson('POST', listUrl, payload);
        }
        recordRequest('POST /cloudartifact/v1/files/list', 'POST', effectiveListUrl, payload, response);
        if (isSessionFailure(response)) {
          sessionExpired = true;
          sessionFailureResponse = response;
          directoryErrors.push({
            parentId: parentId || null,
            path: directoryPath || '/',
            status: response.status,
            message: 'Cookie 或 cftk 已失效',
          });
          return;
        }
        const result = response.body && response.body.result;
        if (!response.ok || !result || !Array.isArray(result.data)) {
          const error = { parentId: parentId || null, path: directoryPath || '/', status: response.status, body: response.body };
          directoryErrors.push(error);
          if (!parentId) throw new Error(`根目录文件列表请求失败，HTTP ${response.status}`);
          return;
        }
        totalPages = Math.max(pageNo, Number(result.totalPages) || 1);
        for (const item of result.data) {
          if (sessionExpired) break;
          if (!item || typeof item !== 'object') continue;
          if (isFolder(item)) {
            folderCount += 1;
            entries.push({ ...item, _list: item });
            const childId = String(item.id || item.fileId || '').trim();
            const childPath = `${String(directoryPath || '').replace(/\/$/, '')}/${item.name || childId}` || '/';
            if (!childId) {
              directoryErrors.push({ name: item.name, path: childPath, message: '文件夹列表项缺少 id' });
            } else {
              await fetchDirectory(childId, childPath);
            }
          } else {
            fileCount += 1;
            await fetchDetail(item);
          }
        }
        pageNo += 1;
      } while (pageNo <= totalPages && !sessionExpired);
    };

    await fetchDirectory('', '/');
    const hasErrors = directoryErrors.length > 0 || detailErrors.length > 0;
    const apiResult = {
      ok: !sessionExpired,
      status: sessionExpired ? Number(sessionFailureResponse && sessionFailureResponse.status) || 401 : (hasErrors ? 207 : 200),
      url: config.apiUrl,
      body: {
        status: sessionExpired ? 'error' : (hasErrors ? 'partial_success' : 'success'),
        error: sessionExpired ? { message: '网页登录会话已失效：Cookie 或 cftk 无效，请重新点击按钮登录' } : null,
        result: { data: entries, totalRecords: entries.length, totalPages: 1, pageNo: 1 },
        errors: { directories: directoryErrors, details: detailErrors },
      },
    };
    writeProgress(config, 'done', `拉取完成：${folderCount} 个文件夹，${fileCount} 个文件，详情成功 ${detailSuccessCount}`, {
      folderCount, fileCount, detailSuccessCount,
      detailErrorCount: detailErrors.length, directoryErrorCount: directoryErrors.length,
    });

    const sessionMeta = {
      savedAt: new Date().toISOString(),
      projectId: config.projectId,
      repositoryUrl: config.repositoryUrl,
      cookieCount: cookies.length,
      cftkCookieName: cftkCookie ? cftkCookie.name : null,
    };
    fs.writeFileSync(path.join(cacheRoot, 'session.json'), JSON.stringify(sessionMeta, null, 2), 'utf8');
    fs.writeFileSync(resultPath, JSON.stringify({
      response: {
        ok: apiResult.ok,
        status: apiResult.status,
        headers: {},
        elapsed_ms: Date.now() - started,
        body: apiResult.body,
        body_preview: '',
        final_url: apiResult.url,
      },
      requestRecords,
      summary: {
        visitedDirectoryCount: visitedFolders.size,
        folderCount,
        fileCount,
        detailSuccessCount,
        directoryErrors,
        detailErrors,
        loginDiagnostics: loginDiagnosticHistory,
      },
      session: sessionMeta,
    }), 'utf8');
  } finally {
    await context.close();
  }
}

main().catch(error => {
  const resultPath = process.argv[3];
  if (resultPath) {
    fs.writeFileSync(resultPath, JSON.stringify({
      error: `${error.name}: ${error.message}`,
      loginDiagnostics: loginDiagnosticHistory,
      downloadDiagnostic: lastDownloadDiagnostic,
    }), 'utf8');
  }
  process.exitCode = 1;
});
