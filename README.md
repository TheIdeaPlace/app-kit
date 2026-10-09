# The Idea Place App Kit

The basics every app from The Idea Place should have, built once and shared: installing, updating,
reporting a bug, handling crashes, and releasing. Before this kit, each app had its own copy of
these, and a fix in one copy never reached the others.

The plan and the decisions behind it are in
[The-Idea-Place-Projects#9](https://github.com/kellylford/The-Idea-Place-Projects/issues/9).

## What's in it

Nothing yet. This is what's planned.

| Folder | What | Used by |
|---|---|---|
| `relay/` | One bug-report relay, a Cloudflare Worker, for every app. It holds one GitHub App key and files each report in the right app's repo, from a list of allowed repos and with a key per app. Replaces the separate relays in QuickMail and Hyper-V Manage. | Every app |
| `dotnet/` | `TheIdeaPlace.AppKit`, a NuGet package: updates through Velopack, Report a Bug (with optional GitHub sign-in), crash handling and the log file, About, and the Beta setting. | QuickMail, Hyper-V Manage, RSS Quick, Live Captions |
| `python/` | `theideaplace-appkit`, a Python package with the same features, for wxPython apps. | The Chat Place, GHManage, TheClaudeHub, IDT, WeatherFast, Scores |
| `.github/workflows/` | Shared release workflows: build, sign with Azure Artifact Signing, `vpk pack`, check every signature, publish. Each app's own release workflow calls these. | Every Windows and Mac app |

## How every app behaves

The checklist lives in the hub repo as `BASELINE.md`. In short:

- **Install:** Velopack on Windows (Setup.exe and a portable zip). A signed, notarized dmg on the Mac.
- **Update:**
  - A quiet check at startup that never takes focus.
  - Help > Check for Updates.
  - A background download, then "Restart to update" or install on exit.
  - A Beta setting for anyone who wants prereleases.
- **Report a Bug:**
  - Help > Report a Bug opens a form, with a preview of exactly what will be sent.
  - The report is filed by the bot through the relay, or under the person's own name if they sign in to GitHub.
  - If the relay can't be reached, a prefilled GitHub issue page opens, with the full report on the clipboard.
- **Crashes:** they're logged. Next time the app starts, it offers to report the crash.

## License

MIT
