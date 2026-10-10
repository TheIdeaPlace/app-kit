/**
 * The Idea Place bug-report relay: one Cloudflare Worker that files bug reports for every app.
 *
 * Built from QuickMail's relay (kellylford/QuickMail, relay/), which Hyper-V Manage then
 * copied. Rather than a Worker and a GitHub App per app, this one holds a single GitHub App
 * key and files each report in the repo of the app that sent it.
 *
 * An app identifies itself with two headers:
 *   X-AppKit-App  the app's id, a key of APPS below
 *   X-AppKit-Key  that app's relay key: the Worker secret APP_KEY_<ID>, or its entry in the
 *                 APP_KEYS secret for the first ten apps
 *
 * Only repos listed in APPS can receive issues, and the app id picks the repo, so a leaked
 * key can file issues in one repo and nowhere else. Keys are per app, so one can be rotated
 * without rebuilding every other app.
 *
 * Plan: kellylford/The-Idea-Place-Projects#9. Issue: TheIdeaPlace/app-kit#1.
 */

/**
 * Every app the relay will file for. A repo that moves (say, into the TheIdeaPlace org) is
 * a one-line change here. The App must also be installed on the new owner.
 */
const APPS = {
  quickmail: { owner: 'kellylford', repo: 'QuickMail' },
  hypervmanage: { owner: 'TheIdeaPlace', repo: 'HyperVManage' },
  rssquick: { owner: 'TheIdeaPlace', repo: 'rssquick' },
  livecaptions: { owner: 'TheIdeaPlace', repo: 'LiveCaptionsWithAccessibility' },
  thechatplace: { owner: 'kellylford', repo: 'AIChat' },
  ghmanage: { owner: 'TheIdeaPlace', repo: 'GHManage' },
  theclaudehub: { owner: 'kellylford', repo: 'TheWorkBench' },
  idt: { owner: 'TheIdeaPlace', repo: 'Image-Description-Toolkit' },
  weatherfast: { owner: 'TheIdeaPlace', repo: 'WeatherFast' },
  scores: { owner: 'TheIdeaPlace', repo: 'Scores' },
};

const USER_AGENT = 'TheIdeaPlace-AppKit-Relay';
// The report's `kind` picks the labels. Without this, ideas sent from an app's Report a Bug
// arrived labelled bug and had to be relabelled by hand (AIChat#131, #134). Anything other
// than "suggestion" is a bug, so older apps that don't send a kind keep working.
const LABELS_FOR_KIND = {
  bug: ['bug', 'user-reported'],
  suggestion: ['enhancement', 'user-reported'],
};

// The apps give up after 15s and fall back to a prefilled issue page. If the relay were still
// working after that, the person could file by hand and the relay could file the same report
// again. So every GitHub call in one request shares a single deadline that ends first.
const DEADLINE_MS = 12_000;

const MAX_BODY_BYTES = 64 * 1024;
const MAX_TITLE_CHARS = 200;
const MAX_FIELD_CHARS = 8_000;
const MAX_CONTACT_CHARS = 200;

export default {
  async fetch(request, env) {
    if (request.method !== 'POST') return text(405, 'Method not allowed.');

    const url = new URL(request.url);
    if (url.pathname !== '/report') return text(404, 'Not found.');

    const appId = (request.headers.get('X-AppKit-App') || '').toLowerCase();
    const target = Object.hasOwn(APPS, appId) ? APPS[appId] : null;
    const expectedKey = target ? appKey(env, appId) : '';
    if (!target || !timingSafeEqual(request.headers.get('X-AppKit-Key') || '', expectedKey)) {
      return text(401, 'Unauthorized.');
    }

    // Fail open on a rate-limiter problem. A misconfigured binding blocking every bug report
    // is worse than the junk issues it would have stopped: junk can be deleted, but a report
    // the person couldn't file is gone.
    try {
      const ip = request.headers.get('CF-Connecting-IP') || 'unknown';
      const { success } = await env.RATE_LIMITER.limit({ key: ip });
      if (!success) return text(429, 'Too many reports from this address. Try again shortly.');
    } catch (err) {
      console.error('rate limiter unavailable, allowing request:', err);
    }

    if (Number(request.headers.get('Content-Length')) > MAX_BODY_BYTES) {
      return text(413, 'Report too large.');
    }
    const raw = await request.text();
    if (byteLength(raw) > MAX_BODY_BYTES) return text(413, 'Report too large.');

    let report;
    try {
      report = JSON.parse(raw);
    } catch {
      return text(400, 'Malformed JSON.');
    }

    // Defuse before cutting, so the added characters can't push a title past GitHub's limit.
    const title = clip(defuseMentions(str(report?.title)), MAX_TITLE_CHARS, ' …');
    const body = clip(defuseMentions(str(report?.body)), MAX_FIELD_CHARS, '\n\n…(truncated)');
    if (!title || !body) return text(400, 'Both title and body are required.');

    const contact = clip(defuseMentions(str(report?.contact)), MAX_CONTACT_CHARS, ' …');
    const issueBody = contact ? `${body}\n\n### Contact\n${contact}\n` : body;

    // DEADLINE_MS in env exists only so tests can use a short deadline.
    const signal = AbortSignal.timeout(Math.max(1, Number(env.DEADLINE_MS) || DEADLINE_MS));
    try {
      const labels = report?.kind === 'suggestion' ? LABELS_FOR_KIND.suggestion : LABELS_FOR_KIND.bug;
      const issue = await createIssue(env, target, { title, body: issueBody }, labels, signal);
      if (typeof issue?.html_url !== 'string') throw new Error('GitHub reply had no html_url');
      return Response.json({ issueUrl: issue.html_url, number: issue.number });
    } catch (err) {
      // The message may carry GitHub's response text. Log it, but tell the caller nothing
      // it could use to probe the App's permissions.
      console.error(`issue creation failed for ${appId}:`, err);
      return text(502, 'Could not create the issue.');
    }
  },
};

/**
 * An app's key is its own secret, APP_KEY_<ID> (for example APP_KEY_THECHATPLACE). That lets
 * scripts/add-app.py add an app without touching, or knowing, any other app's key.
 *
 * The first ten apps were set up with one JSON secret, APP_KEYS (app id -> key). It's still
 * read for any app without its own secret. A missing or malformed value fails closed.
 */
function appKey(env, appId) {
  const own = env[`APP_KEY_${appId.toUpperCase()}`];
  if (typeof own === 'string' && own) return own;
  try {
    const keys = JSON.parse(env.APP_KEYS || '{}');
    const key = keys?.[appId];
    return typeof key === 'string' ? key : '';
  } catch {
    console.error('APP_KEYS is not valid JSON; refusing every report until it is fixed.');
    return '';
  }
}

/**
 * Files the issue. If the cached installation or token turns out to be stale (the App was
 * reinstalled, or the repo was added to it after the token was made), forget them and try
 * once more. That's safe: GitHub refuses those before creating anything.
 */
async function createIssue(env, target, fields, labels, signal) {
  for (let attempt = 1; ; attempt++) {
    try {
      const token = await getInstallationToken(env, target, signal);
      return await postIssue(target, token, fields, labels, signal);
    } catch (err) {
      if (attempt === 1 && err.staleAuth) {
        console.error(`stale GitHub auth for ${repoKey(target)}, retrying with a fresh lookup:`, err);
        forget(target);
        continue;
      }
      throw err;
    }
  }
}

async function postIssue(target, token, fields, labels, signal) {
  const url = `https://api.github.com/repos/${target.owner}/${target.repo}/issues`;
  try {
    return await githubFetch('POST', url, token, { ...fields, labels }, signal);
  } catch (err) {
    if (STALE_AUTH_ON_ISSUE.has(err.status) && !err.rateLimited) err.staleAuth = true;
    // 422 means GitHub created nothing; one cause is a label it won't accept. Filing the
    // report without labels beats losing it. A second 422 is reported as a failure.
    if (err.status !== 422) throw err;
    console.error(`422 for ${target.owner}/${target.repo}, filing without labels:`, err);
    return githubFetch('POST', url, token, fields, signal);
  }
}

// What GitHub answers when a token or installation no longer covers the repo.
const STALE_AUTH_ON_ISSUE = new Set([401, 403, 404]);
const STALE_AUTH_ON_TOKEN = new Set([401, 404]);

// ---------------------------------------------------------------- GitHub auth

// The App is installed once per account (kellylford, and later TheIdeaPlace), and each
// installation has its own id and its own one-hour tokens. Both are cached per isolate: a
// warm instance skips two round-trips, and a cold one looks them up again.
//
// The installation is cached per repo, not per owner. When a repo moves to TheIdeaPlace,
// GitHub redirects its old name, so looking up "kellylford/X" returns the org's installation.
// Caching that under "kellylford" would send every other kellylford app to the wrong
// installation.
const installationIds = new Map(); // "owner/repo" (lower case) -> installation id
const tokens = new Map(); // installation id -> { token, expiresAt }

function repoKey(target) {
  return `${target.owner}/${target.repo}`.toLowerCase();
}

function forget(target) {
  const installationId = installationIds.get(repoKey(target));
  installationIds.delete(repoKey(target));
  if (installationId !== undefined) tokens.delete(installationId);
}

async function getInstallationToken(env, target, signal) {
  let jwt;
  const appJwt = async () => (jwt ??= await createAppJwt(env.GITHUB_APP_ID, env.GITHUB_PRIVATE_KEY));

  let installationId = installationIds.get(repoKey(target));
  if (installationId === undefined) {
    const installation = await githubFetch(
      'GET',
      `https://api.github.com/repos/${target.owner}/${target.repo}/installation`,
      await appJwt(),
      undefined,
      signal,
    );
    installationId = installation.id;
    installationIds.set(repoKey(target), installationId);
  }

  const cached = tokens.get(installationId);
  if (cached && cached.expiresAt > Date.now() + 60_000) return cached.token;

  let result;
  try {
    result = await githubFetch(
      'POST',
      `https://api.github.com/app/installations/${installationId}/access_tokens`,
      await appJwt(),
      undefined,
      signal,
    );
  } catch (err) {
    if (STALE_AUTH_ON_TOKEN.has(err.status)) err.staleAuth = true;
    throw err;
  }
  tokens.set(installationId, { token: result.token, expiresAt: Date.parse(result.expires_at) });
  return result.token;
}

async function createAppJwt(appId, privateKeyPem) {
  const key = await importRsaKey(privateKeyPem);
  const now = Math.floor(Date.now() / 1000);

  // iat is backdated 60s because GitHub rejects a JWT whose iat is in its future, and the
  // Worker's clock and GitHub's can differ by a few seconds.
  const header = b64url(JSON.stringify({ alg: 'RS256', typ: 'JWT' }));
  const payload = b64url(JSON.stringify({ iat: now - 60, exp: now + 540, iss: appId }));
  const signingInput = `${header}.${payload}`;

  const signature = await crypto.subtle.sign(
    'RSASSA-PKCS1-v1_5',
    key,
    new TextEncoder().encode(signingInput),
  );

  return `${signingInput}.${b64urlBytes(new Uint8Array(signature))}`;
}

/**
 * GitHub hands out App private keys in PKCS#1 ("BEGIN RSA PRIVATE KEY"), which WebCrypto
 * cannot import; it only takes PKCS#8. Accept either and wrap PKCS#1 here, so a key pasted in
 * whichever form GitHub gave you just works.
 */
async function importRsaKey(pem) {
  const isPkcs1 = /BEGIN RSA PRIVATE KEY/.test(pem);
  const der = pemBody(pem);
  const pkcs8 = isPkcs1 ? wrapPkcs1AsPkcs8(der) : der;

  return crypto.subtle.importKey(
    'pkcs8',
    pkcs8,
    { name: 'RSASSA-PKCS1-v1_5', hash: 'SHA-256' },
    false,
    ['sign'],
  );
}

function pemBody(pem) {
  const base64 = pem
    .replace(/-----BEGIN [^-]+-----/, '')
    .replace(/-----END [^-]+-----/, '')
    .replace(/\s+/g, '');
  const binary = atob(base64);
  const bytes = new Uint8Array(binary.length);
  for (let i = 0; i < binary.length; i++) bytes[i] = binary.charCodeAt(i);
  return bytes;
}

// PrivateKeyInfo ::= SEQUENCE { version INTEGER, algorithm AlgorithmIdentifier, key OCTET STRING }
function wrapPkcs1AsPkcs8(pkcs1) {
  const version = [0x02, 0x01, 0x00];
  // AlgorithmIdentifier for rsaEncryption (1.2.840.113549.1.1.1) with NULL parameters.
  const algorithm = [
    0x30, 0x0d, 0x06, 0x09, 0x2a, 0x86, 0x48, 0x86,
    0xf7, 0x0d, 0x01, 0x01, 0x01, 0x05, 0x00,
  ];
  const keyOctetString = [0x04, ...derLength(pkcs1.length), ...pkcs1];
  const contents = [...version, ...algorithm, ...keyOctetString];
  return new Uint8Array([0x30, ...derLength(contents.length), ...contents]);
}

function derLength(n) {
  if (n < 0x80) return [n];
  const bytes = [];
  for (let v = n; v > 0; v >>>= 8) bytes.unshift(v & 0xff);
  return [0x80 | bytes.length, ...bytes];
}

// ---------------------------------------------------------------- helpers

async function githubFetch(method, url, token, jsonBody, signal) {
  const response = await fetch(url, {
    method,
    headers: {
      Authorization: `Bearer ${token}`,
      Accept: 'application/vnd.github+json',
      'X-GitHub-Api-Version': '2022-11-28',
      'User-Agent': USER_AGENT,
      ...(jsonBody ? { 'Content-Type': 'application/json' } : {}),
    },
    body: jsonBody ? JSON.stringify(jsonBody) : undefined,
    signal,
  });

  if (!response.ok) {
    const err = new Error(`GitHub ${response.status} for ${method} ${url}: ${await response.text()}`);
    err.status = response.status;
    // GitHub's rate limits also answer 403. Retrying with a fresh token would only add calls
    // while it's limiting us, so those aren't treated as stale auth.
    err.rateLimited =
      response.headers.has('retry-after') || response.headers.get('x-ratelimit-remaining') === '0';
    throw err;
  }
  return response.json();
}

function str(value) {
  return typeof value === 'string' ? value : '';
}

/** Trims, and cuts to `max` UTF-16 units without splitting a surrogate pair (an emoji). */
function clip(value, max, suffix) {
  if (typeof value !== 'string') return '';
  const trimmed = value.trim();
  if (trimmed.length <= max) return trimmed;
  let end = max;
  const last = trimmed.charCodeAt(end - 1);
  if (last >= 0xd800 && last <= 0xdbff) end--;
  return `${trimmed.slice(0, end)}${suffix}`;
}

/**
 * Text from the public goes into a public issue filed by the bot, so a "@name" in it would
 * have the bot notify that person. A zero-width space after the @ keeps the text readable
 * but stops GitHub treating it as a mention. GitHub sees a mention after any non-word
 * character (".@name" and "-@name" count), so only an @ right after a letter, digit or
 * underscore is left alone. That covers email addresses, where a letter or digit always
 * comes just before the @.
 */
function defuseMentions(value) {
  return value.replace(/(?<!\w)@(?=[A-Za-z0-9])/g, '@\u200B');
}

function byteLength(str) {
  return new TextEncoder().encode(str).length;
}

function timingSafeEqual(a, b) {
  if (a.length !== b.length || a.length === 0) return false;
  let diff = 0;
  for (let i = 0; i < a.length; i++) diff |= a.charCodeAt(i) ^ b.charCodeAt(i);
  return diff === 0;
}

function b64url(str) {
  return b64urlBytes(new TextEncoder().encode(str));
}

function b64urlBytes(bytes) {
  let binary = '';
  for (const byte of bytes) binary += String.fromCharCode(byte);
  return btoa(binary).replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/, '');
}

function text(status, message) {
  return new Response(message, { status, headers: { 'Content-Type': 'text/plain' } });
}
