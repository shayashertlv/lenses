"""Guard the diagnostic against old-output writes and lost guide-error cases."""
import copy

import numpy as np
import pytest

from glasses3d.tests.proofs import wp6_s1_replay as replay


@pytest.mark.parametrize('destination', ['proof', 'proof/new', 'existing'])
def test_replay_refuses_existing_or_historical_output_before_loading(tmp_path, destination):
    proof = tmp_path/'proof'
    proof.mkdir()
    (proof/'untouched').write_text('saved evidence')
    (tmp_path/'existing').mkdir()
    with pytest.raises(ValueError, match='new folder outside'):
        replay.replay(proof, tmp_path/'unused.json', tmp_path/destination)
    assert (proof/'untouched').read_text() == 'saved evidence'
    assert not (proof/'new').exists()


def test_error_case_replay_preserves_all_signs_sizes_and_source_lines():
    exact = {'05': {'AB': np.array([[0., 0.], [10., 0.]]),
                    'BC': np.array([[0., 0.], [0., 10.]]),
                    'AC': np.array([[0., 0.], [10., 10.]])},
             '02': {'AB': np.array([[30., 40.], [140., 70.]]),
                    'BC': np.array([[140., 70.], [100., 170.]])}}
    moves = {'05': {'AB': [.8, -.2], 'BC': [.02, -.06], 'AC': [-.2, .2]},
             '02': {'AB': [3., 7.2], 'BC': [-7., 6.9]}}
    measured = {k: {c: line + np.array(moves[k][c])[:, None]*replay.normal(line)
                    for c, line in rows.items()} for k, rows in exact.items()}
    original = copy.deepcopy(measured)
    cases = list(replay.sign_cases(exact, measured, moves))
    assert len(cases) == len({tuple(signs.values()) for signs, _ in cases}) == 16
    for signs, lines in cases:
        for c in measured['05']:
            np.testing.assert_array_equal(lines['05'][c], original['05'][c])
        for c in measured['02']:
            delta = lines['02'][c]-exact['02'][c]
            assert np.linalg.norm(delta, axis=1) == pytest.approx(np.abs(moves['02'][c]))
            assert np.sign(delta @ replay.normal(exact['02'][c])).tolist() == [
                signs[f'02 {c} end {end}'] for end in (0, 1)]
    for k in original:
        for c in original[k]:
            np.testing.assert_array_equal(measured[k][c], original[k][c])


def test_saved_error_inversion_keeps_measured_values_separate():
    a = np.array([[30., 40.], [140., 70.]])
    moves = [3., -7.]
    m = a + np.array(moves)[:, None] * replay.normal(a)
    s1 = {'cue': {'photo_steps': {k: {'AB': {'p0': m[0].tolist(), 'p1': m[1].tolist()}}
                                  for k in ('05', '02')}},
          'lines': {f'{k} AB': {'error_added_px': moves} for k in ('05', '02')}}
    measured, exact, _ = replay.saved_lines(s1)
    np.testing.assert_array_equal(measured['02']['AB'], m)
    np.testing.assert_allclose(exact['02']['AB'], a, atol=1e-7, rtol=0)
    s1['cue']['photo_steps']['02']['AB'] = None
    with pytest.raises(ValueError, match='recorded crease: 02 AB'):
        replay.saved_lines(s1)
