"""Connects one more app to the bug-report relay. Run it from the relay folder:

    python scripts/add-app.py <app id>

Before running it, add the app to APPS in src/index.js, merge that, and deploy, so the relay
knows which repo the app's reports go to.

What it does:
1. Generates a new key for this app only. Other apps' keys aren't touched. If the app already
   has a key, it stops without changing anything, because a new key would cut off every copy
   already installed. Pass --rotate to replace it anyway.
2. Stores the key, on stdin so it's never shown:
   - in the Worker, as APP_KEY_<ID> (wrangler secret put);
   - in this repo, as RELAY_APP_KEY_<ID>, so the smoke-test workflow can use it;
   - in the app's repo, as the secret APPKIT_RELAY_KEY, along with the variable APPKIT_RELAY_URL;
   - in Kelly's signing kit vault (Azure Key Vault theideaplace-kit, see signing/README.md in
     kellylford/The-Idea-Place-Projects), as kit-file-appkit-relay-<id>-key, so a local build on any
     machine can have it: `sync-secrets.py pull-kit` writes it to ~/.signing-kit/appkit-relay-<id>.key,
     where the apps' build scripts look. GitHub secrets can't be read back, so without this copy the
     key exists only where it can't be retrieved. This step needs `az login`; if it can't be done,
     it says so and the rest still counts.
3. Opens the GitHub App's settings in the browser. A person has to add the app's repo
   there. GitHub's API for this needs a sign-in made through the App itself, and gh's
   sign-in isn't one.
   Until they do, the app's reports fail with 502.
4. Prints the command for the smoke test, which proves the whole path works.

Needs: gh signed in with access to the app's repo, and wrangler logged in to Cloudflare. If
that login can see more than one Cloudflare account, set CLOUDFLARE_ACCOUNT_ID first (it's the
CLOUDFLARE_ACCOUNT_ID variable in TheIdeaPlace/app-kit).
"""

import argparse
import json
import os
import re
import secrets
import shutil
import subprocess
import sys
import tempfile
import webbrowser
from pathlib import Path

KIT_REPO = "TheIdeaPlace/app-kit"
KIT_VAULT = "theideaplace-kit"
RELAY_URL = "https://appkit-bug-relay.quickmail.workers.dev"
APP_SLUG = "the-idea-place-bug-reporter"
WRANGLER = "wrangler@4.149.0"
RELAY_DIR = Path(__file__).resolve().parent.parent
IS_WINDOWS = os.name == "nt"


def run(args, stdin=None, cwd=None):
    """Runs a command, passing any secret on stdin. Fails loudly but never echoes stdin."""
    exe = args[0] + (".cmd" if IS_WINDOWS and args[0] == "npx" else "")
    result = subprocess.run(
        [exe, *args[1:]], input=stdin, capture_output=True, cwd=cwd,
        # wrangler prints emoji; Windows' default code page can't decode them.
        text=True, encoding="utf-8", errors="replace",
    )
    if result.returncode != 0:
        sys.exit(f"Failed: {' '.join(args)}\n{result.stderr.strip()}")
    return result.stdout


def store_in_kit_vault(app, key, vault=KIT_VAULT):
    """Keep a copy of the key in the signing kit vault, the way sync-secrets.py push-kit stores a kit
    file: base64 of the file's bytes (az --encoding base64), tagged with the file name pull-kit
    restores it as. Returns (ok, message); never fatal, since the relay works without it."""
    exe = shutil.which("az") or shutil.which("az.cmd")
    item = f"appkit-relay-{app}.key"
    name = f"kit-file-appkit-relay-{app}-key"
    if not exe:
        return False, f"the Azure CLI isn't installed, so the key isn't in the kit vault {vault}"
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / item
        path.write_text(key, encoding="utf-8")
        try:
            result = subprocess.run(
                [exe, "keyvault", "secret", "set", "--vault-name", vault, "-n", name, "--file", str(path),
                 "--encoding", "base64", "--tags", f"kit-item={item}", "-o", "none"],
                capture_output=True, text=True, encoding="utf-8", errors="replace")
        finally:
            path.unlink(missing_ok=True)
    if result.returncode != 0:
        first = next((l.strip() for l in result.stderr.splitlines() if l.strip()), "unknown error")
        return False, f"couldn't store the key in the kit vault {vault} ({first}); run az login and add it with push-kit"
    return True, f"Stored a copy in the kit vault {vault} as {name}."


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("app", help="the app's id in APPS, lower case, e.g. thechatplace")
    parser.add_argument("--relay-url", default=RELAY_URL)
    parser.add_argument(
        "--rotate", action="store_true",
        help="replace an existing key; installed copies of the app stop reaching the relay until the next release",
    )
    args = parser.parse_args()

    app = args.app
    if not re.fullmatch(r"[a-z0-9]+", app):
        sys.exit("The app id must be lower-case letters and digits.")
    repos = json.loads(run(["node", str(RELAY_DIR / "scripts" / "app-ids.mjs"), "--repos"]))
    if app not in repos:
        sys.exit(f"{app} isn't in APPS in src/index.js. Add it, merge, deploy, then run this again.")
    repo = repos[app]

    # A new key replaces the one built into every copy of the app already installed, and the
    # relay prefers APP_KEY_<ID>, so those copies would get 401 until the next release. Only
    # do that when asked to.
    has_key = "APPKIT_RELAY_KEY" in run(["gh", "secret", "list", "-R", repo]).split()
    if has_key and not args.rotate:
        sys.exit(
            f"{repo} already has a relay key, so {app} is already connected; nothing was changed.\n"
            "To replace the key anyway (installed copies stop reaching the relay until the next "
            "release), run this again with --rotate."
        )

    key = secrets.token_urlsafe(32)
    name = f"APP_KEY_{app.upper()}"
    run(["npx", "--yes", WRANGLER, "secret", "put", name], stdin=key, cwd=RELAY_DIR)
    print(f"Set {name} in the Worker.", flush=True)
    run(["gh", "secret", "set", f"RELAY_APP_KEY_{app.upper()}", "-R", KIT_REPO], stdin=key)
    print(f"Set RELAY_APP_KEY_{app.upper()} in {KIT_REPO}, for the smoke test.", flush=True)
    run(["gh", "secret", "set", "APPKIT_RELAY_KEY", "-R", repo], stdin=key)
    run(["gh", "variable", "set", "APPKIT_RELAY_URL", "-R", repo, "--body", f"{args.relay_url.rstrip('/')}/report"])
    print(f"Set APPKIT_RELAY_KEY and APPKIT_RELAY_URL in {repo}.", flush=True)
    ok, message = store_in_kit_vault(app, key)
    print(message if ok else f"Warning: {message}.", flush=True)

    owner = repo.split("/")[0]
    if owner.lower() == "theideaplace":
        settings = f"https://github.com/organizations/{owner}/settings/installations"
    else:
        settings = "https://github.com/settings/installations"
    webbrowser.open(settings)
    print(
        f"\nStill to do, by a person: on the page just opened, choose Configure next to"
        f" '{APP_SLUG}', add {repo} under 'Only select repositories', and Save."
        f"\nThen prove it works:  gh workflow run relay-smoke.yml -R {KIT_REPO} -f app={app}",
        flush=True,
    )


if __name__ == "__main__":
    main()
