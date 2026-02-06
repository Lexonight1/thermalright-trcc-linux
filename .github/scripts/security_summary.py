"""Aggregate security scan results into a single findings summary."""

import json
import os


def main():
    summary = {
        'pip_high': 0,
        'bandit_high': 0,
        'safety_high': 0,
        'total_findings': 0,
    }

    if os.path.exists('pip_audit_results.json'):
        try:
            data = json.load(open('pip_audit_results.json'))
            vulns = data.get('vulns') or data.get('vulnerabilities') or data
            high = sum(
                1 for v in vulns
                if str(v.get('severity', '')).lower() in ('high', 'critical')
            )
            summary['pip_high'] = high
            summary['total_findings'] += len(vulns) if isinstance(vulns, list) else 0
        except Exception:
            pass

    if os.path.exists('bandit_report.json'):
        try:
            data = json.load(open('bandit_report.json'))
            results = data.get('results', [])
            high = sum(1 for r in results if r.get('issue_severity', '').upper() == 'HIGH')
            summary['bandit_high'] = high
            summary['total_findings'] += len(results)
        except Exception:
            pass

    if os.path.exists('safety_report.json'):
        try:
            data = json.load(open('safety_report.json'))
            vulns = (data.get('vulnerabilities') if isinstance(data, dict) else data) or []
            total = len(vulns) if isinstance(vulns, list) else 0
            high = sum(
                1 for v in vulns
                if str(v.get('severity', '')).lower() in ('high', 'critical')
            )
            summary['safety_high'] = high
            summary['total_findings'] += total
        except Exception:
            pass

    with open('security_findings.json', 'w') as f:
        json.dump(summary, f)

    print('Security summary:')
    print(json.dumps(summary))


if __name__ == '__main__':
    main()
