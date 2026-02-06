"""Inspect security findings and set GitHub Actions output."""

import json
import os


def main():
    try:
        with open('security_reports/security_findings.json') as f:
            summary = json.load(f)
    except Exception:
        summary = {'pip_high': 0, 'bandit_high': 0, 'safety_high': 0, 'total_findings': 0}

    print('Summary:', summary)

    found = any(summary.get(k, 0) > 0 for k in ('pip_high', 'bandit_high', 'safety_high'))

    with open(os.environ['GITHUB_OUTPUT'], 'a') as fh:
        fh.write(f'found={str(found).lower()}\n')
        fh.write('summary=' + json.dumps(summary) + '\n')


if __name__ == '__main__':
    main()
