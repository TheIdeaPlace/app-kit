/**
 * Tests for the relay, in plain Node with no test framework, so they run on any machine:
 *
 *   node relay/test/relay.test.js
 *
 * Two groups:
 *  - Key handling and JWT signing, which fail silently and expensively if wrong. These are
 *    carried over from QuickMail's relay.
 *  - The request handler, run against a fake GitHub. It checks that each app reaches only its
 *    own repo, that every way of failing is refused, and that the GitHub calls are right.
 */

import { generateKeyPairSync, createVerify } from 'node:crypto';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, join } from 'node:path';

// The Worker only exports its handler. Evaluate its source with the internals re-exported,
// rather than widening the Worker's own exports just for tests.
const workerSource = readFileSync(
  join(dirname(fileURLToPath(import.meta.url)), '..', 'src', 'index.js'),
  'utf8',
);
const testable = `${workerSource}
export { importRsaKey, createAppJwt, wrapPkcs1AsPkcs8, timingSafeEqual, clip, defuseMentions, APPS, installationIds, tokens };`;
const worker = await import(
  `data:text/javascript;base64,${Buffer.from(testable).toString('base64')}`
);
const handler = worker.default;

const { publicKey, privateKey } = generateKeyPairSync('rsa', { modulusLength: 2048 });
const pkcs1Pem = privateKey.export({ type: 'pkcs1', format: 'pem' });
const pkcs8Pem = privateKey.export({ type: 'pkcs8', format: 'pem' });

let failures = 0;
async function check(name, fn) {
  try {
    await fn();
    console.log(`  ok   ${name}`);
  } catch (err) {
    failures++;
    console.error(`  FAIL ${name}\n       ${err.stack || err.message}`);
  }
}

// ---------------------------------------------------------------- keys and JWT

console.log('keys and JWT');

await check('imports a PKCS#8 key', async () => {
  const key = await worker.importRsaKey(pkcs8Pem);
  assert.equal(key.algorithm.name, 'RSASSA-PKCS1-v1_5');
});

await check('imports a PKCS#1 key, the format GitHub actually downloads', async () => {
  assert.match(pkcs1Pem, /BEGIN RSA PRIVATE KEY/);
  const key = await worker.importRsaKey(pkcs1Pem);
  assert.equal(key.algorithm.name, 'RSASSA-PKCS1-v1_5');
});

await check('PKCS#1 conversion yields byte-identical DER to a real PKCS#8 export', () => {
  const pkcs1Der = privateKey.export({ type: 'pkcs1', format: 'der' });
  const expected = privateKey.export({ type: 'pkcs8', format: 'der' });
  const actual = worker.wrapPkcs1AsPkcs8(new Uint8Array(pkcs1Der));
  assert.deepEqual(Buffer.from(actual), Buffer.from(expected));
});

await check('signs a JWT that verifies against the public key', async () => {
  const jwt = await worker.createAppJwt('123456', pkcs1Pem);
  const [header, payload, signature] = jwt.split('.');
  const verifier = createVerify('RSA-SHA256');
  verifier.update(`${header}.${payload}`);
  assert.ok(verifier.verify(publicKey, Buffer.from(signature, 'base64url')), 'signature did not verify');
});

await check('JWT claims match what GitHub requires', async () => {
  const jwt = await worker.createAppJwt('123456', pkcs8Pem);
  const [headerB64, payloadB64] = jwt.split('.');
  const header = JSON.parse(Buffer.from(headerB64, 'base64url'));
  const claims = JSON.parse(Buffer.from(payloadB64, 'base64url'));
  assert.equal(header.alg, 'RS256');
  assert.equal(claims.iss, '123456');
  const now = Math.floor(Date.now() / 1000);
  assert.ok(claims.iat < now, 'iat must be backdated for clock skew');
  assert.ok(claims.exp - claims.iat <= 600, 'lifetime must stay within 10 minutes');
});

await check('key comparison rejects mismatches and empty keys', () => {
  assert.equal(worker.timingSafeEqual('secret', 'secret'), true);
  assert.equal(worker.timingSafeEqual('secret', 'secrat'), false);
  assert.equal(worker.timingSafeEqual('secret', 'secretx'), false);
  assert.equal(worker.timingSafeEqual('', ''), false);
});

await check('oversized fields are truncated rather than dropped', () => {
  assert.equal(worker.clip('  hello  ', 100, '…'), 'hello');
  assert.equal(worker.clip(undefined, 100, '…'), '');
  assert.equal(worker.clip(null, 100, '…'), '');
  assert.equal(worker.clip('x'.repeat(50), 10, ' …'), `${'x'.repeat(10)} …`);
});

await check('truncation never splits an emoji', () => {
  const clipped = worker.clip(`${'x'.repeat(9)}😀tail`, 10, '');
  assert.equal(clipped, 'x'.repeat(9));
  assert.doesNotThrow(() => encodeURIComponent(clipped));
});

await check('mentions are defused; email addresses are left alone', () => {
  assert.equal(worker.defuseMentions('ping @kellylford please'), 'ping @​kellylford please');
  assert.equal(worker.defuseMentions('@TheIdeaPlace/team'), '@​TheIdeaPlace/team');
  assert.equal(worker.defuseMentions('mail me@example.com'), 'mail me@example.com');
  assert.equal(worker.defuseMentions('a.b+c@example.com'), 'a.b+c@example.com');
  assert.equal(worker.defuseMentions('just an @ sign'), 'just an @ sign');
});

await check('every app maps to an owner and repo', () => {
  for (const [id, target] of Object.entries(worker.APPS)) {
    assert.equal(id, id.toLowerCase(), `${id} must be lower case; the header is lower-cased`);
    assert.ok(target.owner && target.repo, `${id} is missing owner or repo`);
  }
});

// ---------------------------------------------------------------- fake GitHub

const KEYS = { thechatplace: 'tcp-key-123', quickmail: 'qm-key-456' };

/**
 * A fake GitHub. Options let a test change the installation id (as a reinstall would), make
 * the installation lookup, token or issue calls fail, or make the issue call hang.
 * Issue statuses are used in order, and the last one repeats.
 */
function fakeGitHub({
  installationFor = (owner) => (owner === 'kellylford' ? 42 : 99),
  installationStatus = 200,
  tokenStatus = 200,
  issueStatus = [201],
  issueReply = (owner, repo) => ({ html_url: `https://github.com/${owner}/${repo}/issues/7`, number: 7 }),
  hang = false,
} = {}) {
  const calls = [];
  const statuses = [...issueStatus];
  const fake = {
    calls,
    installationFor,
    // A token is only valid for the installation that minted it, and only while that
    // installation is still the current one for its owner.
    validTokens: new Set(),
  };
  globalThis.fetch = async (url, init) => {
    const call = { url, method: init.method, auth: init.headers.Authorization, body: init.body && JSON.parse(init.body) };
    calls.push(call);
    let m;
    if ((m = url.match(/\/repos\/([^/]+)\/([^/]+)\/installation$/)) && init.method === 'GET') {
      if (installationStatus !== 200) return new Response('not installed', { status: installationStatus });
      return Response.json({ id: fake.installationFor(m[1]) });
    }
    if ((m = url.match(/\/app\/installations\/(\d+)\/access_tokens$/)) && init.method === 'POST') {
      const id = Number(m[1]);
      const current = [fake.installationFor('kellylford'), fake.installationFor('TheIdeaPlace')];
      if (!current.includes(id)) return new Response('no such installation', { status: 404 });
      if (tokenStatus !== 200) return new Response('token failure', { status: tokenStatus });
      fake.validTokens.add(`inst-token-${id}`);
      return Response.json({ token: `inst-token-${id}`, expires_at: new Date(Date.now() + 3600_000).toISOString() });
    }
    if ((m = url.match(/\/repos\/([^/]+)\/([^/]+)\/issues$/)) && init.method === 'POST') {
      if (hang) {
        // Node doesn't keep a process alive for an AbortSignal.timeout timer, so hold it open
        // until the abort arrives.
        const keepAlive = setInterval(() => {}, 1000);
        return new Promise((_, reject) => {
          init.signal.addEventListener('abort', () => {
            clearInterval(keepAlive);
            reject(init.signal.reason);
          });
        });
      }
      const token = init.headers.Authorization.replace('Bearer ', '');
      const tokenId = Number(token.replace('inst-token-', ''));
      if (!fake.validTokens.has(token) || tokenId !== fake.installationFor(m[1])) {
        return new Response('Bad credentials', { status: 401 });
      }
      const status = statuses.length > 1 ? statuses.shift() : statuses[0];
      if (status >= 400) return new Response('secret GitHub detail', { status });
      return Response.json(issueReply(m[1], m[2]), { status });
    }
    return new Response('unexpected', { status: 500 });
  };
  return fake;
}

function env(overrides = {}) {
  return {
    GITHUB_APP_ID: '123456',
    GITHUB_PRIVATE_KEY: pkcs1Pem,
    APP_KEYS: JSON.stringify(KEYS),
    RATE_LIMITER: { limit: async () => ({ success: true }) },
    ...overrides,
  };
}

function report({ app = 'thechatplace', key = KEYS.thechatplace, method = 'POST', path = '/report', body } = {}) {
  const headers = { 'Content-Type': 'application/json', 'CF-Connecting-IP': '203.0.113.9' };
  if (app !== null) headers['X-AppKit-App'] = app;
  if (key !== null) headers['X-AppKit-Key'] = key;
  return new Request(`https://relay.example${path}`, {
    method,
    headers,
    body: method === 'POST' ? (body ?? JSON.stringify({ title: 'It broke', body: 'What happened: it broke.' })) : undefined,
  });
}

function reset() {
  worker.installationIds.clear();
  worker.tokens.clear();
}

// Quieten the Worker's own console.error while testing failure paths.
const realError = console.error;
const quiet = async (fn) => {
  console.error = () => {};
  try { return await fn(); } finally { console.error = realError; }
};

// ---------------------------------------------------------------- handler

console.log('request handler');

await check('files the issue in the sending app\'s own repo, as the App', async () => {
  reset();
  const { calls } = fakeGitHub();
  const res = await handler.fetch(report(), env(), {});
  assert.equal(res.status, 200);
  assert.deepEqual(await res.json(), { issueUrl: 'https://github.com/kellylford/AIChat/issues/7', number: 7 });

  const issue = calls.find((c) => c.url.endsWith('/issues'));
  assert.equal(issue.url, 'https://api.github.com/repos/kellylford/AIChat/issues');
  assert.equal(issue.auth, 'Bearer inst-token-42');
  assert.deepEqual(issue.body, { title: 'It broke', body: 'What happened: it broke.', labels: ['bug', 'user-reported'] });
});

await check('app id is matched without regard to case', async () => {
  reset();
  fakeGitHub();
  const res = await handler.fetch(report({ app: 'TheChatPlace' }), env(), {});
  assert.equal(res.status, 200);
});

await check('contact is appended under its own heading', async () => {
  reset();
  const { calls } = fakeGitHub();
  const body = JSON.stringify({ title: 't', body: 'b', contact: 'me@example.com' });
  await handler.fetch(report({ body }), env(), {});
  assert.equal(calls.at(-1).body.body, 'b\n\n### Contact\nme@example.com\n');
});

for (const [name, opts] of [
  ['an unknown app', { app: 'notanapp' }],
  ['no app header', { app: null }],
  ['no key header', { key: null }],
  ['the wrong key', { key: 'tcp-key-124' }],
  ['another app\'s key', { key: KEYS.quickmail }],
  ['an app with no key configured', { app: 'ghmanage', key: '' }],
  ['a prototype name as the app id', { app: '__proto__', key: '' }],
  ['"constructor" as the app id', { app: 'constructor', key: 'x' }],
]) {
  await check(`refuses ${name} with 401 and never calls GitHub`, async () => {
    reset();
    const { calls } = fakeGitHub();
    const res = await handler.fetch(report(opts), env(), {});
    assert.equal(res.status, 401);
    assert.equal(calls.length, 0);
  });
}

await check('a missing APP_KEYS secret refuses everything', async () => {
  reset();
  fakeGitHub();
  const res = await handler.fetch(report(), env({ APP_KEYS: undefined }), {});
  assert.equal(res.status, 401);
});

await check('a malformed APP_KEYS secret refuses everything', async () => {
  reset();
  fakeGitHub();
  const res = await quiet(() => handler.fetch(report(), env({ APP_KEYS: '{not json' }), {}));
  assert.equal(res.status, 401);
});

await check('GET is refused with 405', async () => {
  const res = await handler.fetch(report({ method: 'GET' }), env(), {});
  assert.equal(res.status, 405);
});

await check('a path other than /report is 404', async () => {
  const res = await handler.fetch(report({ path: '/issues' }), env(), {});
  assert.equal(res.status, 404);
});

await check('rate-limited callers get 429', async () => {
  reset();
  const { calls } = fakeGitHub();
  const res = await handler.fetch(report(), env({ RATE_LIMITER: { limit: async () => ({ success: false }) } }), {});
  assert.equal(res.status, 429);
  assert.equal(calls.length, 0);
});

await check('a broken rate limiter lets the report through', async () => {
  reset();
  fakeGitHub();
  const broken = { limit: async () => { throw new Error('binding missing'); } };
  const res = await quiet(() => handler.fetch(report(), env({ RATE_LIMITER: broken }), {}));
  assert.equal(res.status, 200);
});

await check('an oversized report is 413', async () => {
  reset();
  fakeGitHub();
  const body = JSON.stringify({ title: 't', body: 'x'.repeat(70 * 1024) });
  const res = await handler.fetch(report({ body }), env(), {});
  assert.equal(res.status, 413);
});

await check('malformed JSON is 400', async () => {
  const res = await handler.fetch(report({ body: '{nope' }), env(), {});
  assert.equal(res.status, 400);
});

await check('a missing title or body is 400', async () => {
  for (const body of [{ body: 'b' }, { title: 't' }, { title: '   ', body: 'b' }, { title: 5, body: 'b' }]) {
    const res = await handler.fetch(report({ body: JSON.stringify(body) }), env(), {});
    assert.equal(res.status, 400, JSON.stringify(body));
  }
});

await check('a repo missing the labels still gets the issue, without them', async () => {
  reset();
  const { calls } = fakeGitHub({ issueStatus: [422, 201] });
  const res = await quiet(() => handler.fetch(report(), env(), {}));
  assert.equal(res.status, 200);
  const issues = calls.filter((c) => c.url.endsWith('/issues'));
  assert.equal(issues.length, 2);
  assert.equal(issues[1].body.labels, undefined);
});

await check('a GitHub failure is 502 and leaks none of GitHub\'s reply', async () => {
  reset();
  fakeGitHub({ issueStatus: [500] });
  const res = await quiet(() => handler.fetch(report(), env(), {}));
  assert.equal(res.status, 502);
  assert.doesNotMatch(await res.text(), /secret GitHub detail/);
});

await check('a second report reuses the installation id and token', async () => {
  reset();
  const { calls } = fakeGitHub();
  await handler.fetch(report(), env(), {});
  await handler.fetch(report(), env(), {});
  assert.equal(calls.filter((c) => c.url.endsWith('/installation')).length, 1);
  assert.equal(calls.filter((c) => c.url.endsWith('/access_tokens')).length, 1);
  assert.equal(calls.filter((c) => c.url.endsWith('/issues')).length, 2);
});

await check('each owner gets its own installation and token', async () => {
  reset();
  const { calls } = fakeGitHub();
  const original = worker.APPS.quickmail;
  worker.APPS.quickmail = { owner: 'TheIdeaPlace', repo: 'QuickMail' };
  try {
    await handler.fetch(report(), env(), {});
    await handler.fetch(report({ app: 'quickmail', key: KEYS.quickmail }), env(), {});
  } finally {
    worker.APPS.quickmail = original;
  }
  const issues = calls.filter((c) => c.url.endsWith('/issues'));
  assert.equal(issues[0].auth, 'Bearer inst-token-42');
  assert.equal(issues[1].auth, 'Bearer inst-token-99');
  assert.equal(issues[1].url, 'https://api.github.com/repos/TheIdeaPlace/QuickMail/issues');
});

console.log('recovering from stale auth and failures');

await check('a reinstalled App (new installation id) heals on the very next report', async () => {
  reset();
  const fake = fakeGitHub();
  assert.equal((await handler.fetch(report(), env(), {})).status, 200);

  fake.installationFor = (owner) => (owner === 'kellylford' ? 43 : 99); // reinstall
  for (let i = 0; i < 3; i++) {
    const res = await quiet(() => handler.fetch(report(), env(), {}));
    assert.equal(res.status, 200, `report ${i + 1} after the reinstall`);
  }
  assert.equal(worker.installationIds.get('kellylford'), 43);
});

await check('a stale id with an expired token heals too', async () => {
  reset();
  const fake = fakeGitHub();
  await handler.fetch(report(), env(), {});
  worker.tokens.clear(); // as if the cached token had expired
  fake.installationFor = (owner) => (owner === 'kellylford' ? 43 : 99);
  const res = await quiet(() => handler.fetch(report(), env(), {}));
  assert.equal(res.status, 200);
});

await check('retries stale auth only once, so a lasting 401 is a 502', async () => {
  reset();
  const { calls } = fakeGitHub({ issueStatus: [401] });
  // Make every issue POST fail as stale, even with a fresh token.
  const res = await quiet(() => handler.fetch(report(), env(), {}));
  assert.equal(res.status, 502);
  assert.equal(calls.filter((c) => c.url.endsWith('/issues')).length, 2);
});

await check('an App that isn\'t installed is a 502, and nothing is cached', async () => {
  reset();
  fakeGitHub({ installationStatus: 404 });
  const res = await quiet(() => handler.fetch(report(), env(), {}));
  assert.equal(res.status, 502);
  assert.equal(worker.installationIds.size, 0);
});

await check('a failed token mint is a 502', async () => {
  reset();
  fakeGitHub({ tokenStatus: 500 });
  const res = await quiet(() => handler.fetch(report(), env(), {}));
  assert.equal(res.status, 502);
  assert.equal(worker.tokens.size, 0);
});

await check('two 422s are a 502 after exactly two POSTs', async () => {
  reset();
  const { calls } = fakeGitHub({ issueStatus: [422, 422] });
  const res = await quiet(() => handler.fetch(report(), env(), {}));
  assert.equal(res.status, 502);
  assert.equal(calls.filter((c) => c.url.endsWith('/issues')).length, 2);
});

await check('a 422 then a 500 is a 502 after exactly two POSTs', async () => {
  reset();
  const { calls } = fakeGitHub({ issueStatus: [422, 500] });
  const res = await quiet(() => handler.fetch(report(), env(), {}));
  assert.equal(res.status, 502);
  assert.equal(calls.filter((c) => c.url.endsWith('/issues')).length, 2);
});

await check('a hung GitHub call gives up at the deadline with a 502', async () => {
  reset();
  fakeGitHub({ hang: true });
  const started = Date.now();
  const res = await quiet(() => handler.fetch(report(), env({ DEADLINE_MS: '300' }), {}));
  assert.equal(res.status, 502);
  assert.ok(Date.now() - started < 3000, 'should stop near the deadline');
});

await check('a reply without an issue URL is a 502, not an empty 200', async () => {
  reset();
  fakeGitHub({ issueReply: () => ({}) });
  const res = await quiet(() => handler.fetch(report(), env(), {}));
  assert.equal(res.status, 502);
});

for (const value of ['"str"', 'null', '[]', '5', '{"thechatplace": 12345}']) {
  await check(`APP_KEYS of ${value} refuses the report`, async () => {
    reset();
    const { calls } = fakeGitHub();
    const res = await handler.fetch(report(), env({ APP_KEYS: value }), {});
    assert.equal(res.status, 401);
    assert.equal(calls.length, 0);
  });
}

await check('a too-large Content-Length is refused before the body is read', async () => {
  const request = report();
  const headers = new Headers(request.headers);
  headers.set('Content-Length', String(1024 * 1024));
  const res = await handler.fetch(
    { method: 'POST', url: request.url, headers, text: () => { throw new Error('body was read'); } },
    env(),
    {},
  );
  assert.equal(res.status, 413);
});

await check('mentions in the report are defused in the filed issue', async () => {
  reset();
  const { calls } = fakeGitHub();
  const body = JSON.stringify({ title: 'cc @someone', body: 'ping @kellylford', contact: '@me or me@example.com' });
  await handler.fetch(report({ body }), env(), {});
  const filed = calls.at(-1).body;
  assert.equal(filed.title, 'cc @​someone');
  assert.equal(filed.body, 'ping @​kellylford\n\n### Contact\n@​me or me@example.com\n');
});

await check('a long title is cut without adding new lines', async () => {
  reset();
  const { calls } = fakeGitHub();
  await handler.fetch(report({ body: JSON.stringify({ title: 't'.repeat(500), body: 'b' }) }), env(), {});
  const title = calls.at(-1).body.title;
  assert.ok(!title.includes('\n'));
  assert.ok(title.length <= 202);
});

console.log(failures ? `\n${failures} failure(s)` : '\nall passed');
process.exit(failures ? 1 : 0);
