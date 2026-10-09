# Bug-report relay

One Cloudflare Worker that turns a bug report from any of the apps into a GitHub issue in
that app's repo, filed by one GitHub App. Nobody needs a GitHub account to report a bug.

It's built from QuickMail's relay, which Hyper-V Manage then copied. This replaces both,
so each app no longer needs its own Worker, GitHub App and secrets.

Nothing runs until a report arrives, so there's no server to patch, monitor or restart.

## How a report travels

1. The app POSTs to `https://<worker>/report` with JSON `{title, body, contact?}` and two headers:
   - `X-AppKit-App`: the app's id, for example `thechatplace`
   - `X-AppKit-Key`: that app's relay key
2. The Worker checks the key against that app's entry in `APP_KEYS`. It refuses an unknown
   app or a wrong key with 401. Each address is limited to 5 reports a minute.
3. It looks up the GitHub App's installation for the repo's owner and gets a one-hour token
   for that installation.
4. It files the issue in the repo listed for that app in `APPS` (`src/index.js`), with the
   labels `bug` and `user-reported`. If GitHub refuses with 422, nothing was created, so the
   relay tries once more without the labels.
   - If the cached installation or token turns out to be stale, for example after the App was
     reinstalled, it forgets them and tries once more.
   - Any `@name` in the report gets a zero-width space after the @, so the bot won't notify
     people mentioned in it. Email addresses are left alone.
5. It replies with `{issueUrl, number}`. If anything fails, it replies 502 without
   GitHub's error text, and the app falls back to opening a prefilled issue page.
   - All the GitHub calls in one report share a 12-second deadline, which ends before the
     apps give up at 15. That makes it unlikely that the relay files a report the person has
     already filed by hand: it can still happen if GitHub accepts the issue just as the
     deadline passes.

**Everything in a report is public**, including the optional contact field, which is often
an email address. The apps must say so next to that field.

A relay key ships inside the app, so assume people can find it. That's acceptable because
a key can only file issues in its own app's repo, and it can be replaced without touching
any other app.

## Adding an app

1. Add a line to `APPS` in `src/index.js`, then open a PR. The tests check the entry.
2. Install the GitHub App on that repo (step 1 below).
3. Give the app a key:
   - Add it to the `RELAY_APP_KEYS` repo secret here, then run the workflow with
     **sync secrets** on.
   - Set the same key in the app repo's `BUG_REPORT_RELAY_KEY` secret, so its builds include it.

When a repo moves to another owner (for example into TheIdeaPlace), change its `APPS`
line. The Worker finds the new owner's installation by itself.

## Tests

The tests use only the Node standard library, so they run anywhere, Windows ARM64 included:

```
node relay/test/relay.test.js
```

`relay/test/check-app-keys.test.sh` tests the check on `RELAY_APP_KEYS`. It needs jq, so it
runs in CI.

The **Bug-report relay** workflow runs them on every PR and deploys from `main`.

## One-time setup

Deploying runs in CI, because wrangler can't run on Windows ARM64. The deploy step skips,
with a notice, until steps 2 and 3 are done.

### 1. Create the GitHub App (Kelly, in the browser)

At <https://github.com/organizations/TheIdeaPlace/settings/apps/new>:

- **Name:** `The Idea Place Bug Reporter`. Issues show `the-idea-place-bug-reporter[bot]` as the author.
- **Homepage URL:** `https://github.com/TheIdeaPlace/app-kit`
- **Enable Device Flow:** on. Nothing uses it yet. It's for the planned option to sign in from
  inside an app and file under your own name (TheIdeaPlace/app-kit#2). Whether this App's
  user tokens can file in these repos still has to be checked when that's built.
- **Webhook:** uncheck **Active**.
- **Repository permissions → Issues:** **Read and write**. Leave everything else at **No access**.
- **Where can this App be installed:** **Any account**. Today's app repos belong to
  kellylford, not the org, and only an App installable on any account can be installed
  there. It does no harm if a stranger installs it, because the relay only files in repos
  listed in `APPS`.

Then:

- Note the **App ID**, and the **Client ID** for sign-in later. The client ID isn't secret.
- Generate a **private key**. GitHub downloads a `.pem` file once and won't show it again.
- **Install App** on **kellylford**, choosing **Only select repositories**, and pick every repo in `APPS`.
  Install it on **TheIdeaPlace** too, for repos that move there.

### 2. Cloudflare

You can reuse the account that runs QuickMail's relay.

- **The workers.dev subdomain is per account, and permanent.** If the QuickMail account is
  reused, the relay's address becomes `appkit-bug-relay.quickmail.workers.dev`. To get
  `theideaplace` in the address instead, use a new Cloudflare account. On a new account, open
  **Workers & Pages** once and choose a subdomain before the first deploy, or the deploy fails.
- The rate limit's `namespace_id` (1002) is different from the old relay's (1001), so the two
  don't share counters on one account.

- At <https://dash.cloudflare.com/profile/api-tokens>, choose **Create Custom Token**.
- Give it two permissions:
  - `Account / Workers Scripts / Edit`
  - `Account / Account Settings / Read`
- Scope it to your account. No zone is needed.

### 3. Secrets in this repo

Settings → Secrets and variables → Actions:

| Name | Kind | Value |
|---|---|---|
| `CLOUDFLARE_API_TOKEN` | secret | the token from step 2 |
| `CLOUDFLARE_ACCOUNT_ID` | variable | the account ID from the Cloudflare dashboard |
| `RELAY_GITHUB_APP_ID` | secret | the App ID |
| `RELAY_GITHUB_PRIVATE_KEY` | secret | the full contents of the `.pem` file |
| `RELAY_APP_KEYS` | secret | `{"thechatplace":"<random>", ...}`, one random key of at least 16 characters per app |

Paste the private key straight from the file into the GitHub secret form. Never put it in
chat or in a file in a repo. Claude can generate the app keys and set `RELAY_APP_KEYS` and each app's
`BUG_REPORT_RELAY_KEY` with `gh secret set`, so no key passes through a person's hands.

Then run **Bug-report relay** from the Actions tab with **sync secrets** on. The Worker URL
is in the deploy log, as `https://appkit-bug-relay.<subdomain>.workers.dev`. Set it as the
`BUG_REPORT_RELAY_URL` variable in each app repo.

### 4. Smoke test

File one real report, then close the issue it creates. Take the key from wherever it was
generated; don't paste it into chat.

```bash
curl -sS -X POST "https://appkit-bug-relay.<subdomain>.workers.dev/report"   -H "Content-Type: application/json"   -H "X-AppKit-App: thechatplace"   -H "X-AppKit-Key: $THECHATPLACE_RELAY_KEY"   -d '{"title":"Relay smoke test","body":"Testing the shared relay. Close me."}'
```

Expect `{"issueUrl":"https://github.com/kellylford/AIChat/issues/…","number":…}`, with the
issue authored by `the-idea-place-bug-reporter[bot]`. The same request with a wrong key, or
with `X-AppKit-App: quickmail`, must return 401 and file nothing.

## QuickMail's old relay

Copies of QuickMail that are already installed post to `quickmail-bug-relay`. Leave that
Worker running until most people have updated to a QuickMail that uses this one, then
delete it.
