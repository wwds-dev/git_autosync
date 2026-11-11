# Support

git_autosync is a leak-gated auto-commit and push tool for your own GitHub repositories (a CLI engine and a PySide6 window), maintained by one person in their own time. There is no paid
support and no guaranteed response time.

## Where to ask

| You want | Go to |
|---|---|
| Help running it, or a question about how it works | the README and `HANDOVER.md` first, then a [GitHub issue](https://github.com/wwds-dev/git_autosync/issues) |
| To report a bug | a GitHub issue with the version in the window title, or the commit you run from, your macOS version, what you did, what you expected and what happened. Do not paste tokens, gitleaks findings that show a secret, the names of private repositories, or your repo list |
| To report a security problem | [SECURITY.md](SECURITY.md), privately, never in a public issue |
| To suggest a feature | a GitHub issue; `SUGGESTIONS.md` lists what is already considered |

## What to expect

Issues are read **about once a week**; a reply within 7 days is the target, not a promise.
Fixes land when the maintainer has time.

## What git_autosync is not

- A guarantee that no secret is ever pushed. gitleaks blocks the patterns it recognises, in the staged change and in history; a secret it does not recognise is not blocked. Rotate any credential that was ever committed, whatever the scan says.
- A backup of work it never saw: in push-only mode it pushes existing commits and commits nothing.

## Known limits

- macOS only (developed on Apple Silicon); needs `git` and `gitleaks` installed; published as source.
- It pushes with your own git credentials; a push that fails for authentication is reported, never retried with other credentials.
