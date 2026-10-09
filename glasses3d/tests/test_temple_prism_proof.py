"""The P1 verdict must grade the analytic share, not only report it."""
import numpy as np
import pytest

from glasses3d.tests.proofs import wp6_prada as P


def test_analytic_prism_p1_includes_independent_share():
    result = P.prism_instrument_check()
    assert result['pass'] and result['share_check']['pass']
    assert all(np.isfinite(v) for v in result['share_check']['absolute_error'].values())


@pytest.mark.parametrize('fault', ['maximum', 'facet', 'missing', 'nonfinite'])
def test_corrupted_share_cannot_pass_p1(monkeypatch, fault):
    original = P.MS.single_plane_share
    def broken(*args, **kwargs):
        result = original(*args, **kwargs)
        if fault == 'maximum':
            result['share'] += .1
        elif fault == 'facet':
            result['per_plane']['N'] += .1
        elif fault == 'missing':
            del result['per_plane']['N']
        else:
            result['share'] = float('nan')
        return result
    monkeypatch.setattr(P.MS, 'single_plane_share', broken)
    result = P.prism_instrument_check()
    assert not result['pass'] and not result['share_check']['pass']
