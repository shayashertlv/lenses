"""Compare the consensus optical groups of two physical-group corpus reports, design by design.

    python -m qa.compare_group_corpora data/physical-group-corpus-v1/report.json data/physical-group-corpus-roles-v2/report.json

Prints, per design and hypothesis, the consensus optical memberships of both
reports and flags every difference. A role-rule change must be judged here
before any job is rerun; identical memberships mean the change is invisible on
the five development designs.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path


def _hypotheses(report):
    result = {}
    for case in report['cases'] if isinstance(report.get('cases'), list) else report.get('designs', []):
        rows = {}
        for h in case.get('hypotheses', []):
            groups = h.get('consensus_optical_groups') or [g for g in h.get('groups', []) if g.get('role_consensus') == 'optical_candidate']
            rows[h['index']] = sorted(tuple(g['members']) for g in groups)
        result[case['id']] = rows
    return result


def compare(before: Path, after: Path) -> int:
    a, b = (_hypotheses(json.loads(Path(p).read_text('utf-8'))) for p in (before, after))
    differences = 0
    for design in sorted(set(a) | set(b)):
        rows_a, rows_b = a.get(design, {}), b.get(design, {})
        for index in sorted(set(rows_a) | set(rows_b)):
            same = rows_a.get(index) == rows_b.get(index)
            differences += not same
            print(f"{design:18s} h{index}: {'same' if same else 'DIFFERENT'}  before={rows_a.get(index)}  after={rows_b.get(index)}")
    print('differences:', differences)
    return differences


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('before', type=Path)
    parser.add_argument('after', type=Path)
    args = parser.parse_args(argv)
    return 1 if compare(args.before, args.after) else 0


if __name__ == '__main__':
    raise SystemExit(main())
