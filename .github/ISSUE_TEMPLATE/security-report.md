---
name: Security report
about: File a security issue for high/critical findings from automated scans or responsible disclosure
title: '[SECURITY] {{summary}}'
labels: security, automated
assignees: ''

---

**Security report summary**

- **Severity:** [Low / Medium / High / Critical]
- **Scanner/source:** [pip-audit | bandit | codeql | other]
- **CI run / artifact:** (link to workflow run or artifact)
- **Files attached:** (pip_audit.json, bandit.json)

**Description**

Provide a short description of the finding and the vulnerable dependency or source file. Include the affected version and any metadata present in the scanner output.

**Steps to reproduce**

1. (Optional) Steps to reproduce locally or in a minimal test case
2. (Optional) Commands used and environment (OS, Python version)

**Suggested mitigations or references**

- Upgrade to fixed dependency version: `package>=X.Y.Z`
- Patch suggestion or code pointer

**Notes**

- If this is a false positive, add a short justification and close the issue.
- Do not include sensitive information (keys/passwords) in public issues. Use private channels for proof-of-concept if necessary.

---

Thank you for helping keep the project secure. The maintainers will triage and assign this issue for remediation.