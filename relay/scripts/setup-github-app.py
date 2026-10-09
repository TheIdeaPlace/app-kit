"""Creates the relay's GitHub App and sets every secret it needs, without a person handling one.

Run from the relay folder after the Worker has been deployed once:

    python scripts/setup-github-app.py --relay-url https://appkit-bug-relay.<sub>.workers.dev

What happens:
1. A browser page opens with GitHub's "Create GitHub App" form already filled in, from a
   manifest (GitHub's app-manifest flow). The person clicks Create.
2. GitHub sends the browser back to this script with a one-time code. The script trades the
   code for the App's id and private key.
3. It generates a random relay key for each app in APPS (src/index.js).
4. It stores them, piping each value on stdin so nothing is printed or written to the repo:
   - in this repo: RELAY_GITHUB_APP_ID, RELAY_GITHUB_PRIVATE_KEY and RELAY_APP_KEYS, so the
     workflow's "sync secrets" can redo step 5 later;
   - in the Worker: GITHUB_APP_ID, GITHUB_PRIVATE_KEY and APP_KEYS (wrangler secret bulk);
   - in each app's repo: the secret APPKIT_RELAY_KEY and the variable APPKIT_RELAY_URL.
5. It prints the App's install link. Installing still needs a click, because GitHub has no
   API for installing an App on a personal account.

Needs: gh signed in with access to every repo in APPS, and wrangler logged in to Cloudflare.
"""

import argparse
import html
import json
import os
import re
import secrets
import subprocess
import sys
import tempfile
import threading
import urllib.request
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

ORG = "TheIdeaPlace"
KIT_REPO = "TheIdeaPlace/app-kit"
APP_NAME = "The Idea Place Bug Reporter"
PORT = 8765
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


def manifest():
    return {
        "name": APP_NAME,
        "url": f"https://github.com/{KIT_REPO}",
        "description": "Files bug reports sent from inside The Idea Place apps.",
        "hook_attributes": {"url": f"https://github.com/{KIT_REPO}", "active": False},
        "redirect_url": f"http://127.0.0.1:{PORT}/callback",
        "public": True,
        "default_permissions": {"issues": "write"},
        "default_events": [],
    }


def wait_for_code(state):
    """Serves the auto-submitting form and waits for GitHub's redirect with the code."""
    received = {}
    done = threading.Event()

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_GET(self):
            from urllib.parse import parse_qs, urlparse

            url = urlparse(self.path)
            if url.path == "/start":
                action = f"https://github.com/organizations/{ORG}/settings/apps/new?state={state}"
                page = f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<title>Create the GitHub App</title></head><body>
<h1>Create the GitHub App</h1>
<p>Taking you to GitHub. On the next page, check the details and press <strong>Create GitHub App</strong>.</p>
<form id="f" method="post" action="{html.escape(action)}">
<input type="hidden" name="manifest" value="{html.escape(json.dumps(manifest()))}">
<button type="submit">Continue to GitHub</button></form>
<script>document.getElementById('f').submit();</script></body></html>"""
                self.reply(200, page)
            elif url.path == "/callback":
                query = parse_qs(url.query)
                if query.get("state", [""])[0] != state or "code" not in query:
                    self.reply(400, "<p>That didn't come from this setup. Close this tab.</p>")
                    return
                received["code"] = query["code"][0]
                self.reply(
                    200,
                    "<!doctype html><html lang='en'><head><meta charset='utf-8'><title>Done</title></head>"
                    "<body><h1>GitHub App created</h1><p>You can close this tab and go back to Claude.</p></body></html>",
                )
                done.set()
            else:
                self.reply(404, "Not found")

        def reply(self, status, body):
            data = body.encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

    server = HTTPServer(("127.0.0.1", PORT), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    webbrowser.open(f"http://127.0.0.1:{PORT}/start")
    print("Opened the browser. Waiting for you to press Create GitHub App (up to 15 minutes)...", flush=True)
    if not done.wait(timeout=15 * 60):
        server.shutdown()
        sys.exit("Timed out waiting for GitHub.")
    server.shutdown()
    return received["code"]


def convert(code):
    request = urllib.request.Request(
        f"https://api.github.com/app-manifests/{code}/conversions",
        method="POST",
        headers={"Accept": "application/vnd.github+json", "User-Agent": "app-kit-setup"},
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        return json.load(response)


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--relay-url", required=True, help="the deployed Worker's https://... address")
    args = parser.parse_args()
    relay_url = args.relay_url.rstrip("/")
    if not relay_url.startswith("https://"):
        sys.exit("--relay-url must start with https://")

    repos = json.loads(run(["node", str(RELAY_DIR / "scripts" / "app-ids.mjs"), "--repos"]))

    app = convert(wait_for_code(secrets.token_urlsafe(16)))
    app_id, pem = str(app["id"]), app["pem"]
    print(f"Created {app['name']} (App ID {app_id}, slug {app['slug']}).", flush=True)

    keys = {app_name: secrets.token_urlsafe(32) for app_name in repos}
    app_keys = json.dumps(keys)
    # The same rule check-app-keys.sh applies in the workflow (which needs jq, so it isn't run here).
    if not all(re.fullmatch(r"[a-z0-9]+", k) and re.fullmatch(r"[A-Za-z0-9_-]{16,}", v) for k, v in keys.items()):
        sys.exit("Generated keys don't pass the relay's key rule; nothing was set.")

    run(["gh", "secret", "set", "RELAY_GITHUB_APP_ID", "-R", KIT_REPO], stdin=app_id)
    run(["gh", "secret", "set", "RELAY_GITHUB_PRIVATE_KEY", "-R", KIT_REPO], stdin=pem)
    run(["gh", "secret", "set", "RELAY_APP_KEYS", "-R", KIT_REPO], stdin=app_keys)
    print(f"Set the relay's secrets in {KIT_REPO}.", flush=True)

    # wrangler secret bulk reads a file. Keep it in a private temp folder, and delete it even
    # if wrangler fails.
    with tempfile.TemporaryDirectory() as folder:
        secrets_file = Path(folder) / "secrets.json"
        secrets_file.write_text(json.dumps({"GITHUB_APP_ID": app_id, "GITHUB_PRIVATE_KEY": pem, "APP_KEYS": app_keys}))
        run(["npx", "--yes", WRANGLER, "secret", "bulk", str(secrets_file)], cwd=RELAY_DIR)
    print("Set the Worker's secrets.", flush=True)

    for app_name, repo in repos.items():
        run(["gh", "secret", "set", "APPKIT_RELAY_KEY", "-R", repo], stdin=keys[app_name])
        run(["gh", "variable", "set", "APPKIT_RELAY_URL", "-R", repo, "--body", f"{relay_url}/report"])
        print(f"  {app_name}: set APPKIT_RELAY_KEY and APPKIT_RELAY_URL in {repo}", flush=True)

    print(f"\nLast step: install the App. https://github.com/apps/{app['slug']}/installations/new", flush=True)


if __name__ == "__main__":
    main()
