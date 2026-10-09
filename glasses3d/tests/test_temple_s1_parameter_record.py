"""S1's reported starting parameters must describe the mesh actually fitted."""
import copy

import numpy as np
import pytest

from glasses3d.g3d.temple import build_temple
from glasses3d.g3d_host import relief_fit as RF
from glasses3d.tests.proofs import wp6_prada as P


@pytest.mark.parametrize('zone_thickness', [4.8, 6.2])
@pytest.mark.parametrize('end_thickness', ['omitted', None, 4.1])
def test_record_reconstructs_the_effective_initial_mesh(zone_thickness, end_thickness):
    params = RF.prada_base_params(t_zone=zone_thickness)
    params.pop('t_ze', None)
    if end_thickness != 'omitted':
        params['t_ze'] = end_thickness
    before = copy.deepcopy(params)
    family = RF.prada_family(copy.deepcopy(params), topology='pyramid')
    actual = build_temple(family.spec(family.x0()))
    values = P.s1_parameter_values(params, 'pyramid')
    recorded = RF.prada_family({**params, **values}, topology='pyramid')
    reconstructed = build_temple(recorded.spec(recorded.x0()))
    assert np.array_equal(actual.V, reconstructed.V)
    assert np.array_equal(actual.tris, reconstructed.tris)
    assert values['t_ze'] == pytest.approx(actual.spec.thickness(actual.spec.zone_end))
    assert params == before


def test_record_is_a_snapshot_and_excludes_fixed_parameters():
    params = RF.prada_base_params(r_C=3.2)
    values = P.s1_parameter_values(params, 'f1')
    params['r_C'] = 4.7
    assert values['r_C'] == 3.2
    assert 't_ze' not in values
    assert 'r_B' not in values
