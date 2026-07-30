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

function firstText(source, keys) {
  if (!source || typeof source !== 'object') return '';
  for (const key of keys) {
    const value = source[key];
    if (typeof value === 'string' && value.trim()) return value.trim();
  }
  return '';
}

function projectMetadataFromResponse(body, payload, projectId) {
  const result = body && body.result;
  const data = result && Array.isArray(result.data) ? result.data : [];
  const projectItems = data.filter(item =>
    item && typeof item === 'object' &&
    String(item.type || item.nodeType || item.resourceType || '').toLowerCase() === 'project'
  );
  const matchesProject = item => {
    const ids = [
      item.projectId, item.project_id, item.id, item.uuid, item.key,
      item.project && (item.project.id || item.project.projectId),
    ].filter(value => value !== undefined && value !== null).map(String);
    return ids.includes(String(projectId || ''));
  };
  const selected = projectItems.find(matchesProject) || (projectItems.length === 1 ? projectItems[0] : null);
  if (selected) {
    const nestedProject = selected.project && typeof selected.project === 'object' ? selected.project : {};
    const name = firstText(selected, ['projectName', 'project_name', 'displayName', 'display_name', 'name', 'title']) ||
      firstText(nestedProject, ['projectName', 'project_name', 'displayName', 'display_name', 'name', 'title']);
    if (name) {
      return { id: String(projectId || selected.projectId || selected.id || ''), name, source: 'files_list_project' };
    }
  }
  const payloadName = firstText(payload, ['projectName', 'project_name']);
  if (payloadName) {
    return { id: String(projectId || ''), name: payloadName, source: 'files_list_payload' };
  }
  const resultName = firstText(result, ['projectName', 'project_name']);
  if (resultName) {
    return { id: String(projectId || ''), name: resultName, source: 'files_list_result' };
  }
  return null;
}

async function projectMetadataFromPage(page, projectId) {
  const encodedProjectId = encodeURIComponent(String(projectId || ''));
  // The Software Release Repository page shown by Huawei Cloud exposes the
  // authoritative project name in the breadcrumb:
  // 首页 / {项目名称} / 制品仓库 / 软件发布库.
  const breadcrumbSelectors = [
    'nav[aria-label*="breadcrumb" i]',
    '[class*="breadcrumb"]',
    '[class*="Breadcrumb"]',
  ];
  for (const selector of breadcrumbSelectors) {
    try {
      const breadcrumbs = page.locator(selector);
      const count = Math.min(await breadcrumbs.count(), 10);
      for (let index = 0; index < count; index += 1) {
        const breadcrumb = breadcrumbs.nth(index);
        if (!await breadcrumb.isVisible({ timeout: 300 })) continue;
        const text = String(await breadcrumb.innerText() || '');
        const parts = text
          .split(/[/>｜|\n\r]+/)
          .map(value => value.trim())
          .filter(Boolean);
        const repositoryIndex = parts.findIndex(value => value === '制品仓库');
        const name = repositoryIndex > 0 ? parts[repositoryIndex - 1] : '';
        if (name && name !== '首页' && name !== String(projectId || '') && name.length <= 200) {
          return { id: String(projectId || ''), name, source: 'page_breadcrumb' };
        }
      }
    } catch (_) {}
  }

  const selectors = [
    `[data-project-id="${String(projectId || '').replace(/"/g, '\\"')}"][data-project-name]`,
    `[href*="/project/${encodedProjectId}/"][title]`,
    '[class*="project-name"]',
    '[class*="projectName"]',
    '[class*="repository-name"]',
    '[class*="repositoryName"]',
  ];
  for (const selector of selectors) {
    try {
      const matches = page.locator(selector);
      const count = Math.min(await matches.count(), 10);
      for (let index = 0; index < count; index += 1) {
        const element = matches.nth(index);
        if (!await element.isVisible({ timeout: 300 })) continue;
        const name = String(
          await element.getAttribute('data-project-name') ||
          await element.getAttribute('title') ||
          await element.textContent() ||
          ''
        ).trim();
        if (name && name !== String(projectId || '') && name.length <= 200) {
          return { id: String(projectId || ''), name, source: 'page_dom' };
        }
      }
    } catch (_) {}
  }
  return null;
}

function safeUrlForLog(value) {
  try {
    const parsed = new URL(String(value || ''));
    return `${parsed.origin}${parsed.pathname}`;
  } catch (_) {
    return '';
  }
}

function cssAttributeValue(value) {
  return String(value || '').replace(/\\/g, '\\\\').replace(/"/g, '\\"');
}

async function visibleLocatorByHorizontalPosition(locator, preferRight = false) {
  const count = Math.min(await locator.count().catch(() => 0), 100);
  let selected = null;
  for (let index = 0; index < count; index += 1) {
    const candidate = locator.nth(index);
    if (!await candidate.isVisible({ timeout: 250 }).catch(() => false)) continue;
    const box = await candidate.boundingBox().catch(() => null);
    if (!box) continue;
    if (!selected || (preferRight ? box.x > selected.box.x : box.x < selected.box.x)) {
      selected = { locator: candidate, box };
    }
  }
  return selected;
}

async function findRepositoryTreeNodeOnce(page, label, artifactId = '') {
  const selectors = [];
  if (artifactId) {
    const escapedId = cssAttributeValue(artifactId);
    selectors.push(
      `[role="treeitem"][data-key="${escapedId}"]`,
      `[role="treeitem"][data-id="${escapedId}"]`,
      `[data-row-key="${escapedId}"]`,
      `[data-node-id="${escapedId}"]`,
      `[data-file-id="${escapedId}"]`,
    );
  }

  for (const selector of selectors) {
    const selected = await visibleLocatorByHorizontalPosition(page.locator(selector));
    if (selected) {
      const exactChild = label
        ? await visibleLocatorByHorizontalPosition(selected.locator.getByText(label, { exact: true }))
        : null;
      return {
        locator: exactChild ? exactChild.locator : selected.locator,
        row: selected.locator,
        box: exactChild ? exactChild.box : selected.box,
        label,
        source: selector,
      };
    }
  }

  if (!label) return null;
  const selected = await visibleLocatorByHorizontalPosition(page.getByText(label, { exact: true }));
  if (!selected) return null;
  const treeRow = selected.locator.locator(
    'xpath=ancestor-or-self::*[@role="treeitem" or @role="row" or self::li or ' +
    'contains(@class,"tree-node") or contains(@class,"treeNode") or ' +
    'contains(@class,"tree-item") or contains(@class,"treeItem")][1]',
  );
  const row = await visibleLocatorByHorizontalPosition(treeRow);
  return {
    locator: selected.locator,
    row: row ? row.locator : selected.locator,
    box: selected.box,
    label,
    source: 'exact visible text in leftmost repository tree',
  };
}

async function findRepositoryTreeNode(page, label, artifactId = '', loadMore = true) {
  let found = await findRepositoryTreeNodeOnce(page, label, artifactId);
  if (found || !loadMore) return found;

  // The Huawei Cloud tree lazily shows a "加载更多" row.  Keep the search
  // inside the leftmost visible tree and load additional siblings as needed.
  for (let attempt = 1; attempt <= 20 && !found; attempt += 1) {
    const loadMoreNode = await visibleLocatorByHorizontalPosition(
      page.getByText('加载更多', { exact: true }),
    );
    if (!loadMoreNode) break;
    await loadMoreNode.locator.scrollIntoViewIfNeeded().catch(() => {});
    await loadMoreNode.locator.click({ timeout: 10000 });
    await page.waitForTimeout(600);
    found = await findRepositoryTreeNodeOnce(page, label, artifactId);
  }
  return found;
}

async function waitForRepositoryTreeNode(page, label, artifactId = '', timeoutMs = 8000) {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    const found = await findRepositoryTreeNode(page, label, artifactId, false);
    if (found) return found;
    await page.waitForTimeout(300);
  }
  return null;
}

async function expandRepositoryFolder(page, folderName, nextName, navigation) {
  const startedAt = Date.now();
  const node = await findRepositoryTreeNode(page, folderName);
  if (!node) {
    navigation.push({
      stage: 'folder',
      name: folderName,
      outcome: 'not_found',
      elapsed_ms: Date.now() - startedAt,
    });
    throw new Error(`下载目录定位失败：页面左侧目录树中未找到“${folderName}”`);
  }

  const row = node.row || node.locator;
  await node.locator.scrollIntoViewIfNeeded().catch(() => {});
  await node.locator.click({ timeout: 15000 });
  await page.waitForTimeout(500);
  let nextNode = nextName
    ? await waitForRepositoryTreeNode(page, nextName, '', 1200)
    : null;
  let expansionMethod = 'folder label click';

  if (nextName && !nextNode) {
    const toggleSelectors = [
      '[aria-expanded="false"]',
      '[class*="switcher"]',
      '[class*="toggle"]',
      '[class*="expand"]',
      '[class*="fold"]',
      '[class*="arrow"]',
      'button',
    ];
    let toggled = false;
    for (const selector of toggleSelectors) {
      const toggle = await visibleLocatorByHorizontalPosition(row.locator(selector));
      if (!toggle) continue;
      await toggle.locator.click({ timeout: 10000 });
      toggled = true;
      expansionMethod = `folder expansion control: ${selector}`;
      await page.waitForTimeout(700);
      break;
    }
    nextNode = await waitForRepositoryTreeNode(page, nextName, '', 2500);
    if (!nextNode && !toggled) {
      await node.locator.dblclick({ timeout: 10000 });
      expansionMethod = 'folder label double click';
      await page.waitForTimeout(700);
      nextNode = await waitForRepositoryTreeNode(page, nextName, '', 2500);
    }
  }

  navigation.push({
    stage: 'folder',
    name: folderName,
    next_name: nextName || '',
    outcome: !nextName || nextNode ? 'opened' : 'child_not_found',
    method: expansionMethod,
    source: node.source,
    elapsed_ms: Date.now() - startedAt,
  });
  if (nextName && !nextNode) {
    throw new Error(`下载目录展开失败：已找到“${folderName}”，但未显示下一级“${nextName}”`);
  }
}

async function selectRepositoryFile(page, target, navigation) {
  const artifactName = String(target.artifactName || '').trim();
  const artifactId = String(target.artifactId || '').trim();
  if (!artifactName) throw new Error('下载目标缺少文件名，无法在页面目录树中定位');
  const startedAt = Date.now();
  const node = await findRepositoryTreeNode(page, artifactName, artifactId);
  if (!node) {
    navigation.push({
      stage: 'file',
      name: artifactName,
      artifact_id: artifactId,
      outcome: 'not_found',
      elapsed_ms: Date.now() - startedAt,
    });
    throw new Error(`下载文件定位失败：页面左侧目录树中未找到“${artifactName}”`);
  }

  await node.locator.scrollIntoViewIfNeeded().catch(() => {});
  const infoResponsePromise = page.waitForResponse(
    response => /\/cloudartifact\/v1\/files\/[^/?]+\/info(?:[?]|$)/i.test(response.url()),
    { timeout: 15000 },
  ).catch(() => null);
  await node.locator.click({ timeout: 15000 });
  const infoResponse = await infoResponsePromise;
  await page.waitForTimeout(700);
  navigation.push({
    stage: 'file',
    name: artifactName,
    artifact_id: artifactId,
    outcome: 'selected',
    source: node.source,
    info_status: infoResponse ? infoResponse.status() : null,
    info_url: infoResponse ? safeUrlForLog(infoResponse.url()) : '',
    elapsed_ms: Date.now() - startedAt,
  });
}

async function visibleDownloadUrlCandidate(locator, labelBox = null) {
  const count = Math.min(await locator.count().catch(() => 0), 400);
  let selected = null;
  for (let index = 0; index < count; index += 1) {
    const candidate = locator.nth(index);
    if (!await candidate.isVisible({ timeout: 200 }).catch(() => false)) continue;
    const href = String(await candidate.getAttribute('href').catch(() => '') || '');
    const dataHref = String(
      await candidate.getAttribute('data-href').catch(() => '') ||
      await candidate.getAttribute('data-url').catch(() => '') ||
      '',
    );
    const title = String(await candidate.getAttribute('title').catch(() => '') || '');
    const value = String(await candidate.getAttribute('value').catch(() => '') || '');
    const text = String(await candidate.textContent().catch(() => '') || '').trim();
    const displayedUrl = [href, dataHref, title, value, text]
      .find(item => /^https?:\/\//i.test(String(item || '').trim())) || '';
    if (!displayedUrl) continue;
    const box = await candidate.boundingBox().catch(() => null);
    if (!box) continue;
    const centerY = box.y + (box.height / 2);
    const labelCenterY = labelBox ? labelBox.y + (labelBox.height / 2) : centerY;
    const rowDistance = Math.abs(centerY - labelCenterY);
    const leftPenalty = labelBox && box.x <= labelBox.x ? 100000 : 0;
    const score = leftPenalty + (rowDistance * 100) + Math.min(displayedUrl.length, 1000);
    if (!selected || score < selected.score) {
      selected = { locator: candidate, box, score, displayedUrl, href, text };
    }
  }
  return selected;
}

async function clickableDownloadUrlLocator(candidate) {
  const clickableAncestor = candidate.locator.locator(
    'xpath=ancestor-or-self::*[self::a or self::button or @role="link" or @onclick or ' +
    'contains(translate(@class,"LINK","link"),"link") or ' +
    'contains(translate(@class,"URL","url"),"url")][1]',
  );
  if (
    await clickableAncestor.count().catch(() => 0) > 0 &&
    await clickableAncestor.first().isVisible({ timeout: 200 }).catch(() => false)
  ) {
    return clickableAncestor.first();
  }
  // React/Vue often attaches the handler through runtime properties, so the
  // DOM has no onclick attribute. Clicking the visible URL leaf still bubbles
  // to the component handler.
  return candidate.locator;
}

async function findDownloadAddressLinkOnce(page) {
  const labels = page.getByText('下载地址', { exact: true });
  const labelCount = Math.min(await labels.count().catch(() => 0), 20);
  for (let labelIndex = 0; labelIndex < labelCount; labelIndex += 1) {
    const label = labels.nth(labelIndex);
    if (!await label.isVisible({ timeout: 250 }).catch(() => false)) continue;
    const labelBox = await label.boundingBox().catch(() => null);
    let container = label;
    for (let depth = 1; depth <= 6; depth += 1) {
      container = container.locator('xpath=..');
      const links = container.locator('a[href], [role="link"]');
      const linkCount = Math.min(await links.count().catch(() => 0), 20);
      for (let linkIndex = 0; linkIndex < linkCount; linkIndex += 1) {
        const link = links.nth(linkIndex);
        if (!await link.isVisible({ timeout: 250 }).catch(() => false)) continue;
        const href = String(await link.getAttribute('href').catch(() => '') || '');
        const text = String(await link.textContent().catch(() => '') || '').trim();
        if (/\/files\/download|download\?/i.test(href) || /\/files\/download|https?:\/\//i.test(text)) {
          return {
            locator: link,
            source: `right detail row labeled 下载地址 (ancestor depth ${depth})`,
            href: safeUrlForLog(href),
          };
        }
      }

      // Huawei Cloud currently renders the blue address as a custom clickable
      // span/div on some deployments instead of a semantic <a href="...">.
      const textUrls = container.locator(
        'span, div, p, td, dd, code, input, [title^="http"], [data-href^="http"], [data-url^="http"]',
      );
      const urlCandidate = await visibleDownloadUrlCandidate(textUrls, labelBox);
      if (urlCandidate) {
        return {
          locator: await clickableDownloadUrlLocator(urlCandidate),
          source: `visible URL beside 下载地址 (ancestor depth ${depth})`,
          href: safeUrlForLog(urlCandidate.displayedUrl),
          element: {
            tag: await urlCandidate.locator.evaluate(element => element.tagName).catch(() => ''),
            class_name: String(await urlCandidate.locator.getAttribute('class').catch(() => '') || ''),
            role: String(await urlCandidate.locator.getAttribute('role').catch(() => '') || ''),
          },
        };
      }
    }
  }

  const downloadLinks = page.locator(
    'a[href*="/files/download"], a[href*="download?"], [role="link"][data-href*="/files/download"]',
  );
  const selected = await visibleLocatorByHorizontalPosition(downloadLinks, true);
  if (selected) {
    const href = String(await selected.locator.getAttribute('href').catch(() => '') || '');
    return {
      locator: selected.locator,
      source: 'rightmost visible file download link',
      href: safeUrlForLog(href),
    };
  }

  const globalTextUrls = page.locator(
    'span, div, p, td, dd, code, input, [title^="http"], [data-href^="http"], [data-url^="http"]',
  );
  const globalUrlCandidate = await visibleDownloadUrlCandidate(globalTextUrls);
  if (!globalUrlCandidate) return null;
  return {
    locator: await clickableDownloadUrlLocator(globalUrlCandidate),
    source: 'visible HTTP URL text in file detail',
    href: safeUrlForLog(globalUrlCandidate.displayedUrl),
    element: {
      tag: await globalUrlCandidate.locator.evaluate(element => element.tagName).catch(() => ''),
      class_name: String(await globalUrlCandidate.locator.getAttribute('class').catch(() => '') || ''),
      role: String(await globalUrlCandidate.locator.getAttribute('role').catch(() => '') || ''),
    },
  };
}

async function findDownloadAddressLink(page, timeoutMs = 15000) {
  const deadline = Date.now() + timeoutMs;
  do {
    const found = await findDownloadAddressLinkOnce(page);
    if (found) return found;
    await page.waitForTimeout(300);
  } while (Date.now() < deadline);
  return null;
}

async function collectDownloadAddressDiagnostics(page) {
  const labels = page.getByText('下载地址', { exact: true });
  const labelCount = Math.min(await labels.count().catch(() => 0), 20);
  let visibleLabelCount = 0;
  for (let index = 0; index < labelCount; index += 1) {
    if (await labels.nth(index).isVisible({ timeout: 100 }).catch(() => false)) visibleLabelCount += 1;
  }
  const candidates = page.locator(
    'a, [role="link"], span, div, p, td, dd, code, input, [title], [data-href], [data-url]',
  );
  const count = Math.min(await candidates.count().catch(() => 0), 1200);
  const visibleUrlElements = [];
  for (let index = 0; index < count && visibleUrlElements.length < 20; index += 1) {
    const candidate = candidates.nth(index);
    if (!await candidate.isVisible({ timeout: 80 }).catch(() => false)) continue;
    const values = [
      await candidate.getAttribute('href').catch(() => ''),
      await candidate.getAttribute('data-href').catch(() => ''),
      await candidate.getAttribute('data-url').catch(() => ''),
      await candidate.getAttribute('title').catch(() => ''),
      await candidate.getAttribute('value').catch(() => ''),
      await candidate.textContent().catch(() => ''),
    ].map(value => String(value || '').trim());
    const urlValue = values.find(value => /^https?:\/\//i.test(value));
    if (!urlValue) continue;
    const box = await candidate.boundingBox().catch(() => null);
    visibleUrlElements.push({
      tag: await candidate.evaluate(element => element.tagName).catch(() => ''),
      class_name: String(await candidate.getAttribute('class').catch(() => '') || '').slice(0, 300),
      role: String(await candidate.getAttribute('role').catch(() => '') || ''),
      safe_url: safeUrlForLog(urlValue),
      x: box ? Math.round(box.x) : null,
      y: box ? Math.round(box.y) : null,
      width: box ? Math.round(box.width) : null,
      height: box ? Math.round(box.height) : null,
    });
  }
  return {
    download_address_label_count: labelCount,
    download_address_visible_label_count: visibleLabelCount,
    visible_url_elements: visibleUrlElements,
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
  let capturedRemoteProject = null;
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
      capturedRemoteProject = capturedRemoteProject ||
        projectMetadataFromResponse(body, payload, config.projectId);
      if (body.result.data.some(item => String(item && item.type || '').toLowerCase() === 'project')) return;
      if (String(payload.projectId || '') !== String(config.projectId || '')) return;
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
    capturedRemoteProject = capturedRemoteProject ||
      await projectMetadataFromPage(page, config.projectId);
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

    let remoteProjectRequestRecord = null;
    if (!config.downloadTarget && !capturedRemoteProject) {
      const projectListPayload = {
        ...capturedListTemplate.payload,
        pageNo: 1,
        pageSize: Math.max(50, Number(capturedListTemplate.payload.pageSize) || 50),
      };
      delete projectListPayload.projectId;
      delete projectListPayload.parentId;
      const projectListUrl = `${capturedListUrl.split('?')[0]}?_=${Date.now()}`;
      const projectListResponse = await requestJson('POST', projectListUrl, projectListPayload);
      capturedRemoteProject = projectMetadataFromResponse(
        projectListResponse.body,
        projectListPayload,
        config.projectId,
      );
      remoteProjectRequestRecord = {
        interface: 'POST /cloudartifact/v1/files/list (project metadata)',
        method: 'POST',
        url: projectListUrl,
        payload: {
          ...projectListPayload,
          _request_debug: projectListResponse.request_debug || {},
        },
        response: projectListResponse,
      };
    }

    // Download mode deliberately follows the same browser UI workflow that
    // succeeds in Huawei Cloud: open each folder in the left repository tree,
    // select the file, then click the blue link in the right-side "下载地址"
    // row.  The URL is retained only as a local artifact identity; it is never
    // fetched through request.fetch() and is never passed to page.goto().
    if (config.downloadTarget && config.downloadOutputPath) {
      const downloadStartedAt = Date.now();
      const target = config.downloadTarget || {};
      const folderSegments = Array.isArray(target.folderSegments)
        ? target.folderSegments.map(value => String(value || '').trim()).filter(Boolean)
        : [];
      const navigation = [];
      lastDownloadDiagnostic = {
        interface: 'Browser UI directory tree + 下载地址 click',
        method: 'CLICK',
        url: safeUrlForLog(page.url()),
        payload: {
          artifact_name: String(target.artifactName || ''),
          display_path: String(target.displayPath || ''),
          folder_segments: folderSegments,
          artifact_id: String(target.artifactId || ''),
        },
        response: {
          status: 0,
          elapsed_ms: 0,
          received_bytes: 0,
          navigation,
        },
      };

      writeProgress(config, 'download_directory', '正在按目录层级定位制品', {
        artifactName: String(target.artifactName || ''),
        displayPath: String(target.displayPath || ''),
        folderSegments,
      });
      for (let index = 0; index < folderSegments.length; index += 1) {
        const nextName = folderSegments[index + 1] || String(target.artifactName || '');
        await expandRepositoryFolder(page, folderSegments[index], nextName, navigation);
      }

      writeProgress(config, 'download_file', `正在选择文件：${String(target.artifactName || '')}`);
      await selectRepositoryFile(page, target, navigation);

      writeProgress(config, 'download_link', '已打开文件详情，正在点击右侧“下载地址”链接');
      const linkStartedAt = Date.now();
      const downloadLink = await findDownloadAddressLink(page);
      if (!downloadLink) {
        const domDiagnostics = await collectDownloadAddressDiagnostics(page);
        navigation.push({
          stage: 'download_address',
          label: '下载地址',
          outcome: 'not_found',
          dom_diagnostics: domDiagnostics,
          elapsed_ms: Date.now() - linkStartedAt,
        });
        throw new Error('下载地址定位失败：文件详情已打开，但未找到右侧“下载地址”链接');
      }

      navigation.push({
        stage: 'download_address',
        label: '下载地址',
        outcome: 'clicking',
        source: downloadLink.source,
        href: downloadLink.href,
        element: downloadLink.element || null,
        elapsed_ms: Date.now() - linkStartedAt,
      });
      await downloadLink.locator.scrollIntoViewIfNeeded().catch(() => {});
      const [nativeDownload] = await Promise.all([
        page.waitForEvent('download', { timeout: 180000 }),
        downloadLink.locator.click({ timeout: 30000 }),
      ]);
      const nativeFailure = await nativeDownload.failure();
      if (nativeFailure) throw new Error(`浏览器下载失败：${nativeFailure}`);
      await nativeDownload.saveAs(config.downloadOutputPath);

      const stats = fs.statSync(config.downloadOutputPath);
      const receivedBytes = Number(stats.size || 0);
      if (!receivedBytes) throw new Error('浏览器下载失败：下载文件为空');
      const suggestedFilename = nativeDownload.suggestedFilename();
      navigation[navigation.length - 1] = {
        ...navigation[navigation.length - 1],
        outcome: 'download_received',
        suggested_filename: suggestedFilename,
        received_bytes: receivedBytes,
        elapsed_ms: Date.now() - linkStartedAt,
      };
      lastDownloadDiagnostic.response = {
        status: 200,
        headers: {
          'content-disposition': `attachment; filename="${suggestedFilename}"`,
          'content-length': String(receivedBytes),
          'content-type': 'application/octet-stream',
        },
        request_debug: {
          transport: 'browser_ui_click',
          direct_api_fetch_used: false,
          direct_url_navigation_used: false,
          download_link_source: downloadLink.source,
        },
        elapsed_ms: Date.now() - downloadStartedAt,
        received_bytes: receivedBytes,
        navigation,
      };
      writeProgress(config, 'done', `制品下载完成：${receivedBytes} 字节`, {
        downloadedBytes: receivedBytes,
        httpStatus: 200,
        browserNativeDownload: true,
        downloadWorkflow: 'directory_tree_file_download_address_click',
      });
      fs.writeFileSync(resultPath, JSON.stringify({
        response: {
          ok: true,
          status: 200,
          body: { result: { downloaded: true, receivedBytes, suggestedFilename } },
        },
        requestRecords: [lastDownloadDiagnostic],
        loginDiagnostics: loginDiagnosticHistory,
      }), 'utf8');
      return;
    }

    const entries = [];
    const requestRecords = [];
    if (remoteProjectRequestRecord) requestRecords.push(remoteProjectRequestRecord);
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

    const fetchDetail = async (item, directoryPath) => {
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
        entries.push({
          ...item,
          ...bodyResult,
          id: item.id,
          _directory_path: directoryPath || '/',
          _list: { ...item, _directory_path: directoryPath || '/' },
          _detail: bodyResult,
        });
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
            const childId = String(item.id || item.fileId || '').trim();
            const childPath = `${String(directoryPath || '').replace(/\/$/, '')}/${item.name || childId}` || '/';
            entries.push({ ...item, _directory_path: childPath, _list: { ...item, _directory_path: childPath } });
            if (!childId) {
              directoryErrors.push({ name: item.name, path: childPath, message: '文件夹列表项缺少 id' });
            } else {
              await fetchDirectory(childId, childPath);
            }
          } else {
            fileCount += 1;
            await fetchDetail(item, directoryPath);
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
        remoteProjectName: capturedRemoteProject && capturedRemoteProject.name || '',
        remoteProjectSource: capturedRemoteProject && capturedRemoteProject.source || '',
      },
      session: sessionMeta,
      project: capturedRemoteProject || {
        id: String(config.projectId || ''),
        name: '',
        source: 'not_found',
      },
    }), 'utf8');
  } finally {
    await context.close();
  }
}

if (require.main === module) {
  main().catch(error => {
    const resultPath = process.argv[3];
    if (lastDownloadDiagnostic) {
      lastDownloadDiagnostic.response = {
        ...(lastDownloadDiagnostic.response || {}),
        status: 500,
        error: `${error.name}: ${error.message}`,
      };
    }
    if (resultPath) {
      fs.writeFileSync(resultPath, JSON.stringify({
        error: `${error.name}: ${error.message}`,
        loginDiagnostics: loginDiagnosticHistory,
        downloadDiagnostic: lastDownloadDiagnostic,
      }), 'utf8');
    }
    process.exitCode = 1;
  });
}

module.exports = {
  expandRepositoryFolder,
  findDownloadAddressLink,
  findRepositoryTreeNode,
  projectMetadataFromPage,
  selectRepositoryFile,
};
