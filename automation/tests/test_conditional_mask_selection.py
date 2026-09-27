"""Exhaustive finite references for the independent conditional mask selector."""
from copy import deepcopy
from fractions import Fraction
import itertools
import json
import math
import random
import unittest

from reconstruction.conditional_mask_selection import ConditionalMaskPolicy, select_photo_mask_states


def state(identifier, energy, count):
    return {'state_id': identifier, 'energy': energy, 'count': count,
            'provenance': {'region_id': 'source-region', 'hypothesis_id': identifier,
                           'method': 'fixed synthetic conditional energy'}}


def block(identifier, states, group='g'):
    return {'block_id': identifier, 'group_id': group, 'states': states}


def exact_result(report):
    value = report['selected']['mean_energy_exact']
    return Fraction(int(value['numerator']), int(value['denominator']))


def exhaustive(blocks, minimum):
    best, alternatives = None, set()
    for choices in itertools.product(*(b['states'] for b in blocks)):
        counts = {g: 0 for g in minimum}
        for b, s in zip(blocks, choices):
            counts[b['group_id']] += s['count']
        if any(counts[g] < minimum[g] for g in minimum):
            continue
        denominator = sum(counts.values())
        ratio = sum((Fraction(float(s['energy'])) for s in choices), Fraction())/denominator
        identity = tuple(sorted((b['block_id'], s['state_id']) for b, s in zip(blocks, choices)))
        if best is None or ratio < best:
            best, alternatives = ratio, {identity}
        elif ratio == best:
            alternatives.add(identity)
    return best, alternatives


def dag_paths(report):
    dag = report['optimal_selection_dag']
    nodes = {n['node_id']: n for n in dag['nodes']}
    def paths(identifier):
        node = nodes[identifier]
        if not node['predecessors']:
            return [()]
        return [(*previous, (edge['block_id'], edge['state_id']))
                for edge in node['predecessors'] for previous in paths(edge['previous'])]
    return {tuple(sorted(itertools.chain.from_iterable(parts)))
            for parts in itertools.product(*(paths(t['node_id']) for t in dag['terminal_nodes']))}


class ConditionalMaskSelectionTests(unittest.TestCase):
    def solve(self, blocks, minimum, **kwargs):
        result = select_photo_mask_states(blocks, minimum, **kwargs)
        self.assertFalse(result['accepted'])
        self.assertEqual(result['quality_verdict'], 'unmeasured')
        json.dumps(result, allow_nan=False)
        return result

    def test_ratio_counterexample_does_not_choose_each_blocks_independent_mean(self):
        blocks = [block('a', [state('low-local-mean', .4, 1), state('better-photo-mean', 50., 100)]),
                  block('b', [state('fixed', 10., 1)])]
        report = self.solve(blocks, {'g': 1})
        self.assertEqual(report['status'], 'optimal')
        self.assertEqual(exact_result(report), Fraction(60, 101))
        self.assertEqual(report['selected']['states'][0]['state_id'], 'better-photo-mean')

    def test_support_constraints_exclude_a_zero_cost_under_supported_state(self):
        blocks = [block('a', [state('invalid-small', 0., 1), state('valid', 2., 2)])]
        report = self.solve(blocks, {'g': 2})
        self.assertEqual(exact_result(report), Fraction(1))
        self.assertEqual(report['selected']['states'][0]['state_id'], 'valid')

    def test_empty_states_and_group_specific_support_do_not_drop_difficult_groups(self):
        blocks = [block('easy', [state('empty', 0., 0), state('nonempty', 0., 8)], 'a'),
                  block('hard', [state('empty', 0., 0), state('nonempty', 3., 3)], 'b')]
        report = self.solve(blocks, {'a': 1, 'b': 2})
        self.assertEqual(report['selected']['count_by_group'], {'a': 8, 'b': 3})
        self.assertEqual(exact_result(report), Fraction(3, 11))
        impossible = self.solve(blocks, {'a': 1, 'b': 4})
        self.assertEqual(impossible['status'], 'infeasible')
        self.assertTrue(impossible['complete'])
        self.assertIsNone(impossible['selected'])
        self.assertFalse(impossible['conditional_optimum_proven'])

    def test_saturated_support_retains_actual_denominator_and_all_ties(self):
        blocks = [block('a', [state('one', .1, 1), state('two', .2, 2)])]
        report = self.solve(blocks, {'g': 1})
        self.assertEqual(report['optimal_selection_dag']['combination_count'], '2')
        self.assertEqual(dag_paths(report), {(('a', 'one'),), (('a', 'two'),)})
        self.assertEqual(report['selected']['unique_training_count'], 1)
        self.assertEqual(exact_result(report), Fraction(.1))

    def test_randomized_small_problems_match_exhaustive_optimum_and_every_tie(self):
        rng = random.Random(413)
        for case in range(180):
            groups = ['g0', 'g1'] if case % 2 else ['g0']
            blocks = []
            for i in range(rng.randint(len(groups), 6)):
                states = []
                for j in range(rng.randint(1, 3)):
                    count = rng.randint(0, 7)
                    energy = rng.randint(0, 12)/rng.choice([1., 2., 10.]) if count else 0.
                    states.append(state(f's{j}', energy, count))
                blocks.append(block(f'b{i}', states, groups[i % len(groups)]))
            minimum = {g: rng.randint(1, 8) for g in groups}
            expected, ties = exhaustive(blocks, minimum)
            with self.subTest(case=case):
                report = self.solve(blocks, minimum)
                if expected is None:
                    self.assertEqual(report['status'], 'infeasible')
                else:
                    self.assertEqual(report['status'], 'optimal')
                    self.assertEqual(exact_result(report), expected)
                    self.assertEqual(dag_paths(report), ties)
                    self.assertEqual(int(report['optimal_selection_dag']['combination_count']), len(ties))
                    self.assertEqual(report['iterations'][-1]['reduced_cost_integer'], '0')

    def test_compact_tie_dag_does_not_enumerate_the_global_cartesian_product(self):
        blocks = [block(f'b{i:02d}', [state(f's{j}', 1., 1) for j in range(10)]) for i in range(20)]
        report = self.solve(blocks, {'g': 20}, policy=ConditionalMaskPolicy(maximum_transitions=1000))
        dag = report['optimal_selection_dag']
        self.assertEqual(dag['combination_count'], str(10**20))
        self.assertEqual(len(dag['nodes']), 21)
        self.assertEqual(sum(len(n['predecessors']) for n in dag['nodes']), 200)
        self.assertEqual(report['work']['transitions'], 200)
        self.assertEqual(len(report['input']['blocks']), 20)

    def test_adjacent_binary64_costs_are_not_merged_by_a_stop_or_tie_tolerance(self):
        blocks = [block('a', [state('a-worse', math.nextafter(1., math.inf), 1), state('z-better', 1., 1)])]
        report = self.solve(blocks, {'g': 1})
        self.assertEqual(report['selected']['states'][0]['state_id'], 'z-better')
        self.assertEqual(report['optimal_selection_dag']['combination_count'], '1')
        self.assertEqual(exact_result(report), Fraction(1))

    def test_subnormal_mean_underflow_keeps_its_nonzero_exact_ratio(self):
        energy = math.ulp(0.)
        report = self.solve([block('a', [state('tiny', energy, 10)])], {'g': 1})
        self.assertEqual(report['selected']['mean_energy_float'], 0.)
        self.assertEqual(exact_result(report), Fraction(energy)/10)
        self.assertGreater(exact_result(report), 0)
        self.assertTrue(report['conditional_optimum_proven'])

    def test_integer_work_and_iteration_budgets_never_return_a_partial_optimum(self):
        blocks = [block('a', [state('large', 100., 100), state('small', 0., 1)])]
        for policy, reason in [
            (ConditionalMaskPolicy(maximum_iterations=1), 'maximum_iterations'),
            (ConditionalMaskPolicy(maximum_transitions=1), 'maximum_transitions'),
            (ConditionalMaskPolicy(maximum_dp_nodes=1), 'maximum_dp_nodes'),
            (ConditionalMaskPolicy(maximum_dp_edges=1), 'maximum_dp_edges'),
            (ConditionalMaskPolicy(maximum_integer_bits=3), 'maximum_integer_bits')]:
            with self.subTest(reason=reason):
                report = self.solve(blocks, {'g': 1}, policy=policy)
                self.assertEqual(report['status'], 'incomplete')
                self.assertFalse(report['complete'])
                self.assertFalse(report['conditional_optimum_proven'])
                self.assertEqual(report['reason'], reason)
                self.assertIsNone(report['optimal_selection_dag'])
                self.assertEqual(len(report['input']['blocks'][0]['states']), 2)

    def test_integer_cross_product_growth_is_budgeted_after_input_conversion(self):
        # Input energies fit four bits, but the reduced-cost cross products do not.
        blocks = [block('a', [state('large', 14., 15), state('small', 13., 1)])]
        report = self.solve(blocks, {'g': 1}, policy=ConditionalMaskPolicy(maximum_integer_bits=4))
        self.assertEqual(report['status'], 'incomplete')
        self.assertEqual(report['reason'], 'maximum_integer_bits')
        self.assertGreater(report['work']['largest_integer_bits'], 4)

    def test_full_binary64_dynamic_range_has_finite_output_and_exact_stopping(self):
        blocks = [block('a', [state('tiny', math.ulp(0.), 1), state('huge', float.fromhex('0x1.fffffffffffffp+1023'), 1)])]
        report = self.solve(blocks, {'g': 1})
        self.assertEqual(report['status'], 'optimal')
        self.assertEqual(exact_result(report), Fraction(math.ulp(0.)))
        self.assertGreater(report['work']['largest_integer_bits'], 2000)

    def test_exact_tie_count_growth_also_consumes_integer_budget(self):
        blocks = [block(f'b{i}', [state('a', 0., 1), state('b', 0., 1)]) for i in range(10)]
        report = self.solve(blocks, {'g': 1}, policy=ConditionalMaskPolicy(maximum_integer_bits=8))
        self.assertEqual(report['status'], 'incomplete')
        self.assertEqual(report['reason'], 'maximum_integer_bits')
        self.assertIsNone(report['optimal_selection_dag'])

    def test_input_provenance_is_preserved_and_order_does_not_change_result(self):
        blocks = [block('b', [state('two', 2., 2), state('one', 1., 1)], 'g1'),
                  block('a', [state('zero', 0., 1)], 'g0')]
        blocks[0]['states'][0]['provenance']['decoder_alternatives'] = ['original-2', 'alias-5']
        before = deepcopy(blocks)
        first = self.solve(blocks, {'g1': 1, 'g0': 1})
        self.assertEqual(blocks, before)
        blocks.reverse()
        for b in blocks:
            b['states'].reverse()
        second = self.solve(blocks, {'g0': 1, 'g1': 1})
        self.assertEqual(first, second)
        saved = next(s for b in first['input']['blocks'] for s in b['states'] if s['state_id'] == 'two')
        self.assertEqual(saved['provenance']['decoder_alternatives'], ['original-2', 'alias-5'])

    def test_invalid_schema_counts_energy_and_size_are_rejected(self):
        valid = [block('a', [state('ok', 1., 1)])]
        bad_states = [state('bad', float('nan'), 1), state('bad', float('inf'), 1), state('bad', -1., 1),
                      state('bad', True, 1), state('bad', 1., True), state('bad', 1., -1),
                      state('bad', 1., 0), state('bad', 1., 1.5), state('bad', 2**53+1, 1)]
        for invalid in bad_states:
            with self.subTest(state=invalid), self.assertRaises(ValueError):
                self.solve([block('a', [invalid])], {'g': 1})
        for minimum in ({}, {'g': 0}, {'g': True}, {'g': 1, 'other': 1}):
            with self.subTest(minimum=minimum), self.assertRaises(ValueError):
                self.solve(valid, minimum)
        with self.assertRaisesRegex(ValueError, 'state capacity'):
            self.solve([block('a', [state('one', 1., 1), state('two', 2., 1)])], {'g': 1},
                       policy=ConditionalMaskPolicy(maximum_total_states=1))
        with self.assertRaisesRegex(ValueError, 'globally unique'):
            self.solve(valid*2, {'g': 1})
        with self.assertRaisesRegex(ValueError, 'unique within'):
            self.solve([block('a', [state('same', 1., 1), state('same', 2., 2)])], {'g': 1})
        with self.assertRaisesRegex(ValueError, 'finite JSON'):
            invalid = deepcopy(valid); invalid[0]['states'][0]['provenance']['bad'] = float('nan')
            self.solve(invalid, {'g': 1})


if __name__ == '__main__':
    unittest.main()
