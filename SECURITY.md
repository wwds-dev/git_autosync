<!-- Owner: enable GitHub's private vulnerability reporting for wwds-dev/git_autosync (Settings > Code security > Private vulnerability reporting); the form linked below does not work until then. Delete this comment afterwards. -->

# Security

git_autosync is free, open-source software maintained by one person. It commits and pushes to your repositories with your credentials, and decides from a secret scan whether a push may go out,
so security reports are welcome.

## Report privately

Use GitHub's private report form: **Security > Report a vulnerability** on
<https://github.com/wwds-dev/git_autosync/security/advisories/new>. Please do not open a public issue for a vulnerability.

Include the version in the window title, or the commit you run from, what you did, and what happened. Leave out real keys, tokens and personal data:
describe them instead.

## What to expect

- **Acknowledgement within 7 days.**
- **A first assessment within 14 days**, and a fix or a written reason within 90 days.
- No bounty, and no service-level promise beyond this: one person keeps this project, in
  their own time. If a deadline slips, the report gets a reply saying so.
- Credit in the CHANGELOG if you want it.

## Supported versions

Only the latest commit on `master` is supported. There are no binary releases; git_autosync is
distributed as source.

## Scope

In scope: this repository: the engine (`git_autosync.sh`), the window (`app/`) and the restore script.
Out of scope: vulnerabilities in git, gitleaks, GitHub, Qt, PySide6 or Python (report those upstream); a secret gitleaks does not detect, which is a detection gap to report to gitleaks unless this tool skipped the scan; and problems that need an attacker who already controls your Mac.
