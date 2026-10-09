# Bug-report relay

One Cloudflare Worker that turns a bug report from any of the apps into a GitHub issue in
that app's repo, filed by one GitHub App. Nobody needs a GitHub account to report a bug.

It's built from QuickMail's relay, which Hyper-V Manage then copied. This replaces both,
so each app no longer needs its own Worker, GitHub App and secrets.

Nothing runs until a report arrives, so there's no server to patch, monitor or restart.

## How a report travels

1. The app POSTs to `https://<worker>/report` with JSON `{title, body, contact?, kind?}` and two headers.
   `kind` is `"bug"` (the default) or `"suggestion"`. The headers are:
   - `X-AppKit-App`: the app's id, for example `thechatplace`
   - `X-AppKit-Key`: that app's relay key
2. The Worker checks the key against that app's own secret, `APP_KEY_<ID>`, or its entry in
   `APP_KEYS` for the first ten apps. It refuses an unknown
   app or a wrong key with 401. Each address is limited to 5 reports a minute.
3. It looks up the GitHub App's installation for the repo's owner and gets a one-hour token
   for that installation.
4. It files the issue in the repo listed for that app in `APPS` (`src/index.js`), with the
   labels `bug` and `user-reported`, or `enhancement` and `user-reported` for a suggestion.
   If GitHub refuses with 422, nothing was created, so the
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

Every step matters. In particular, if step 4 is skipped, the app's reports fail with 502.

1. **Add the app to `APPS`** in `src/index.js`: one line with its id and repo. Then open a PR;
   the tests check the entry.
2. **Deploy**, from `relay/` on a PC where wrangler is logged in:
   `npx wrangler@4.149.0 deploy`.
3. **Give the app a key:** `python scripts/add-app.py <app id>`. The script:
   - generates a key for this app only;
   - stores it in the Worker as `APP_KEY_<ID>`, and in this repo as `RELAY_APP_KEY_<ID>` for the smoke test;
   - puts `APPKIT_RELAY_KEY` and `APPKIT_RELAY_URL` in the app's repo.

   No other app's key changes, so copies already installed keep working.
4. **Add the repo to the GitHub App** (a person has to do this). The script opens the page.
   Choose **Configure** next to *the-idea-place-bug-reporter*, add the repo under
   **Only select repositories**, then **Save**. GitHub has no API for changing an installation
   on a personal account, so this can't be scripted.
5. **Run the smoke test:** `gh workflow run relay-smoke.yml -R TheIdeaPlace/app-kit -f app=<app id>`.
   It files one real report and checks that bad keys are refused. Close the test issue it files.

**When a repo moves to another owner,** for example into TheIdeaPlace:
- change its `APPS` line and deploy;
- install the App on the new owner, if it isn't already, and add the repo there.

The Worker finds the new owner's installation by itself. The repo's secrets move with it.

## Tests

The tests use only the Node standard library, so they run anywhere, Windows ARM64 included:

```
node relay/test/relay.test.js
```

`relay/test/check-app-keys.test.sh` tests the check on `RELAY_APP_KEYS`. It needs jq, so it
runs in CI.

The **Bug-report relay** workflow runs both on every PR. It deploys from `main` only once a
Cloudflare API token is set (see below). Until then, deploy from a PC.

## How it's set up (done 2026-10-09)

- **Worker:** `appkit-bug-relay` on Kelly's Cloudflare account (the one QuickMail's relay uses),
  at `https://appkit-bug-relay.quickmail.workers.dev`.
  - The `quickmail` subdomain belongs to the account and can't be changed. People never see
    the address, because it's built into the apps.
  - The rate limit's `namespace_id` (1002) differs from the old relay's (1001), so the two
    don't share counters.
- **Deploying from this PC works.** wrangler 4.149.0 runs on Windows ARM64. That corrects
  QuickMail's older note. `npx wrangler@4.149.0 login` signs in through the browser.
  - The sign-in waits only about two minutes after the Allow page opens.
  - It runs a small listener on this PC to catch the answer, so it has to keep running
    until Allow is pressed.
- **GitHub App:** *The Idea Place Bug Reporter*, App ID 5251631, owned by TheIdeaPlace, set
  to be installable on any account so it can go on kellylford's repos.
  - Its only permission is to read and write issues. It has no webhook.
  - It was created with `scripts/setup-github-app.py`, which uses GitHub's app-manifest flow: a
    person only presses **Create**, and the private key goes straight into secrets, never
    shown to anyone.
- **Secrets:**
  - In the Worker: `GITHUB_APP_ID`, `GITHUB_PRIVATE_KEY`, `APP_KEYS` (the first ten apps),
    and `APP_KEY_<ID>` for apps added later.
  - In this repo: `RELAY_GITHUB_APP_ID`, `RELAY_GITHUB_PRIVATE_KEY`, `RELAY_APP_KEYS` and
    `RELAY_APP_KEY_<ID>`, plus the variable `CLOUDFLARE_ACCOUNT_ID`.
  - In each app's repo: `APPKIT_RELAY_KEY` and the variable `APPKIT_RELAY_URL`.
- **Device flow is off.** It's for the planned "file under my own name" sign-in
  (TheIdeaPlace/app-kit#2). Turn it on in the App's settings when that's built.

### Optional: let CI deploy

Create a Cloudflare API token at <https://dash.cloudflare.com/profile/api-tokens>, choosing
**Create Custom Token**:
- two permissions, `Account / Workers Scripts / Edit` and `Account / Account Settings / Read`;
- scoped to your account, with no zone.

Paste it straight into this repo's `CLOUDFLARE_API_TOKEN` secret (Settings → Secrets and
variables → Actions), never into chat. From then on, every merge to `main` that touches
`relay/` deploys by itself.

### Redoing the secrets

The workflow's **sync secrets** option pushes `RELAY_GITHUB_APP_ID`,
`RELAY_GITHUB_PRIVATE_KEY` and `RELAY_APP_KEYS` back into the Worker. It needs the API token
above. It doesn't push `APP_KEY_<ID>`; run `add-app.py` again for those.

## QuickMail's old relay

Copies of QuickMail that are already installed post to `quickmail-bug-relay`. Leave that
Worker running until most people have updated to a QuickMail that uses this one, then
delete it.
