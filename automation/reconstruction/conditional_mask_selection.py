"""Bounded conditional mask selection for supplied numerical block energies.

Blocks are caller-asserted disjoint unique-pixel sets, each belonging to one
group. Every state supplies its energy SUM, unique training count and provenance.
States must already satisfy all other model-validity rules. This module cannot
verify those assertions from aggregate counts. It neither fits optical material
parameters nor preserves/certifies an uncertainty ensemble.

The finite binary64 energies are accumulated on an exact integer lattice. A
saturated-count DP solves each group's constrained reduced-cost problem;
Dinkelbach iteration finds the minimum photo energy/count ratio. Equality and
termination are exact for the supplied numerical energies, with no tie epsilon.
Input energies themselves are numerical approximations, not exact optical costs.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from fractions import Fraction
import hashlib
import json
import math
from numbers import Real
from pathlib import Path


@dataclass(frozen=True)
class ConditionalMaskPolicy:
    maximum_blocks: int = 256
    maximum_states_per_block: int = 1024
    maximum_total_states: int = 16_384
    maximum_provenance_bytes: int = 2_000_000
    maximum_count: int = 1_000_000_000
    maximum_support: int = 4096
    maximum_iterations: int = 128
    maximum_dp_nodes: int = 200_000
    maximum_dp_edges: int = 1_000_000
    maximum_transitions: int = 2_000_000
    maximum_integer_bits: int = 4096

    def __post_init__(self):
        for name, value in asdict(self).items():
            if type(value) is not int or value < 1:
                raise ValueError(f'{name} must be a positive integer')


class _WorkBudget(Exception):
    pass


def _text(value, name):
    if not isinstance(value, str) or not value.strip() or len(value) > 256:
        raise ValueError(f'{name} must be a nonempty string of at most 256 characters')
    return value


def _encoded(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False).encode('utf-8')


def _inputs(blocks, minimum_counts, policy):
    if not isinstance(blocks, list) or not blocks:
        raise ValueError('blocks must be a nonempty list')
    if len(blocks) > policy.maximum_blocks:
        raise ValueError('Input budget exceeded: maximum_blocks')
    result, block_ids, state_count, provenance_bytes = [], set(), 0, 0
    for block in blocks:
        if not isinstance(block, dict) or set(block) != {'block_id', 'group_id', 'states'}:
            raise ValueError('Each block requires exactly block_id, group_id and states')
        bid = _text(block['block_id'], 'block_id'); gid = _text(block['group_id'], 'group_id')
        if bid in block_ids:
            raise ValueError('block_id must be globally unique')
        block_ids.add(bid)
        states = block['states']
        if not isinstance(states, list) or not states:
            raise ValueError('Every block requires at least one state')
        state_count += len(states)
        if len(states) > policy.maximum_states_per_block or state_count > policy.maximum_total_states:
            raise ValueError('Input budget exceeded: state capacity')
        normalized, state_ids = [], set()
        for state in states:
            if not isinstance(state, dict) or set(state) != {'state_id', 'energy', 'count', 'provenance'}:
                raise ValueError('Each state requires exactly state_id, energy, count and provenance')
            sid = _text(state['state_id'], 'state_id')
            if sid in state_ids:
                raise ValueError('state_id must be unique within its block')
            state_ids.add(sid)
            supplied = state['energy']
            if isinstance(supplied, bool) or not isinstance(supplied, Real):
                raise ValueError('State energy must be a finite nonnegative binary64-representable number')
            try:
                energy = float(supplied)
            except (OverflowError, ValueError) as error:
                raise ValueError('State energy must be finite') from error
            if not math.isfinite(energy) or energy < 0 or energy != supplied:
                raise ValueError('State energy must be finite, nonnegative and exactly representable in binary64')
            count = state['count']
            if type(count) is not int or count < 0:
                raise ValueError('State count must be a nonnegative integer')
            if count > policy.maximum_count:
                raise ValueError('Input budget exceeded: maximum_count')
            if count == 0 and energy != 0:
                raise ValueError('An empty unique-pixel set must have zero energy')
            provenance = state['provenance']
            if not isinstance(provenance, dict) or not provenance:
                raise ValueError('Each state requires nonempty finite JSON provenance')
            try:
                raw = _encoded(provenance)
                provenance = json.loads(raw)
            except (ValueError, TypeError, RecursionError) as error:
                raise ValueError('State provenance must contain finite JSON values') from error
            provenance_bytes += len(raw)
            if provenance_bytes > policy.maximum_provenance_bytes:
                raise ValueError('Input budget exceeded: maximum_provenance_bytes')
            normalized.append({'state_id': sid, 'energy': energy if energy else 0., 'count': count, 'provenance': provenance})
        result.append({'block_id': bid, 'group_id': gid, 'states': sorted(normalized, key=lambda s: s['state_id'])})
    result.sort(key=lambda b: (b['group_id'], b['block_id']))
    groups = {b['group_id'] for b in result}
    if not isinstance(minimum_counts, dict) or set(minimum_counts) != groups:
        raise ValueError('minimum_counts must name every block group exactly once')
    for gid, count in minimum_counts.items():
        if type(count) is not int or count < 1:
            raise ValueError('Each observed group needs positive minimum support')
        if count > policy.maximum_support:
            raise ValueError('Input budget exceeded: maximum_support')
    if sum(max(s['count'] for s in b['states']) for b in result) > policy.maximum_count:
        raise ValueError('Input budget exceeded: maximum possible photo count')
    return {'blocks': result, 'minimum_counts': dict(sorted(minimum_counts.items()))}


class _Budget:
    def __init__(self, policy):
        self.policy = policy
        self.counts = {'transitions': 0, 'dp_nodes': 0, 'dp_edges': 0, 'largest_integer_bits': 0}

    def integer(self, value):
        bits = abs(value).bit_length()
        self.counts['largest_integer_bits'] = max(self.counts['largest_integer_bits'], bits)
        if bits > self.policy.maximum_integer_bits:
            raise _WorkBudget('maximum_integer_bits')
        return value

    def take(self, kind):
        self.counts[kind] += 1
        if self.counts[kind] > getattr(self.policy, 'maximum_'+kind):
            raise _WorkBudget('maximum_'+kind)


def _group_dp(blocks, minimum, numerator, denominator, budget, group_number):
    """Return all reduced-cost-minimizing feasible paths as a predecessor DAG."""
    nodes = []
    def create(count, level):
        budget.take('dp_nodes')
        value = {'id': f'g{group_number}-n{len(nodes)}', 'count': count, 'level': level,
                 'score': None, 'edges': [], 'ways': 0, 'chosen': (), 'energy': 0, 'total_count': 0}
        nodes.append(value)
        return value
    first = create(0, 0); first.update(score=0, ways=1)
    current = {0: first}
    for level, block in enumerate(blocks, 1):
        following = {}
        for previous in current.values():
            for state in block['states']:
                budget.take('transitions')
                support = min(minimum, previous['count']+state['count'])
                positive = budget.integer(state['_units']*denominator)
                negative = budget.integer(numerator*state['count'])
                score = budget.integer(previous['score']+budget.integer(positive-negative))
                target = following.get(support)
                if target is None:
                    target = create(support, level); following[support] = target
                if target['score'] is not None and score > target['score']:
                    continue
                budget.take('dp_edges')
                edge = {'previous': previous['id'], 'block_id': block['block_id'], 'state_id': state['state_id']}
                chosen = (*previous['chosen'], state['state_id'])
                energy = budget.integer(previous['energy']+state['_units'])
                count = budget.integer(previous['total_count']+state['count'])
                if target['score'] is None or score < target['score']:
                    target.update(score=score, edges=[edge], ways=previous['ways'], chosen=chosen, energy=energy, total_count=count)
                else:
                    target['edges'].append(edge)
                    target['ways'] = budget.integer(target['ways']+previous['ways'])
                    if chosen < target['chosen']:
                        target.update(chosen=chosen, energy=energy, total_count=count)
        current = following
    terminal = current.get(minimum)
    if terminal is None:
        raise RuntimeError('Feasible support preflight disagrees with the conditional DP')
    return {'group_id': blocks[0]['group_id'], 'blocks': blocks, 'nodes': nodes, 'terminal': terminal}


def _selected(blocks, choices, exponent, budget):
    energy = count = 0
    counts, selected = {}, []
    for block, sid in zip(blocks, choices):
        state = next(s for s in block['states'] if s['state_id'] == sid)
        energy = budget.integer(energy+state['_units'])
        count = budget.integer(count+state['count'])
        counts[block['group_id']] = counts.get(block['group_id'], 0)+state['count']
        selected.append({'block_id': block['block_id'], 'group_id': block['group_id'], 'state_id': sid})
    denominator = budget.integer(count << exponent)
    ratio = Fraction(energy, denominator)
    return {'states': selected, 'unique_training_count': count, 'count_by_group': counts,
            'energy_lattice_units': str(energy), 'mean_energy_float': float(ratio),
            'mean_energy_exact': {'numerator': str(ratio.numerator), 'denominator': str(ratio.denominator)}}, energy, count


def _tie_dag(results, budget):
    nodes, terminals, combinations = [], [], 1
    for result in results:
        terminal = result['terminal']
        combinations = budget.integer(combinations*terminal['ways'])
        mapping = {node['id']: node for node in result['nodes']}
        pending, visited = [terminal['id']], set()
        while pending:
            identifier = pending.pop()
            if identifier in visited:
                continue
            visited.add(identifier)
            node = mapping[identifier]
            pending.extend(edge['previous'] for edge in node['edges'])
        for node in result['nodes']:
            if node['id'] in visited:
                nodes.append({'node_id': node['id'], 'group_id': result['group_id'], 'block_level': node['level'],
                    'support_count_capped': node['count'], 'ways': str(node['ways']), 'predecessors': node['edges']})
        terminals.append({'group_id': result['group_id'], 'node_id': terminal['id']})
    return {'representation': 'Cartesian product of group predecessor DAGs; paths select one state per block',
            'combination_count': str(combinations), 'terminal_nodes': terminals, 'nodes': nodes,
            'tie_rule': 'exact reduced-cost equality for supplied binary64 energies; no tolerance merging',
            'selected_representative': 'lexicographically smallest state-id sequence in sorted group/block order'}


def select_photo_mask_states(blocks: list[dict], minimum_counts: dict[str, int], *,
                            policy=ConditionalMaskPolicy()) -> dict:
    """Minimize total supplied energy / total unique count for one photograph.

    Input blocks: {block_id,group_id,states:[{state_id,energy,count,provenance}]}.
    Counts refer to unique training pixels, already deduplicated inside a state.
    Distinct blocks must be pixel-disjoint, and every block belongs to one group.
    Minimum support applies per group. Unsupported metadata/model states must be
    excluded by the caller with their rejection evidence retained separately.

    Invalid inputs or exceeded input-size capacities raise ValueError before
    solving. Runtime/iteration/integer/DAG budgets return status=incomplete;
    selected, when present, is only a feasible upper bound. status=optimal proves
    the finite supplied-energy conditional optimum, never an outer fit optimum.
    All input alternatives and provenance are retained with an input SHA256.
    """
    if not isinstance(policy, ConditionalMaskPolicy):
        raise ValueError('Expected ConditionalMaskPolicy')
    request = _inputs(blocks, minimum_counts, policy)
    minimum_counts = request['minimum_counts']
    report = {'schema_version': 1, 'method': 'conditional_photo_mask_ratio_v1', 'status': 'incomplete',
        'complete': False, 'conditional_optimum_proven': False, 'accepted': False, 'quality_verdict': 'unmeasured',
        'input': request, 'input_sha256': hashlib.sha256(_encoded(request)).hexdigest(), 'policy': asdict(policy),
        'implementation_sha256': hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        'selected': None, 'optimal_selection_dag': None, 'iterations': [],
        'scope': 'Finite conditional minimum for supplied numerical block energies only',
        'limitations': ['Pixel disjointness, within-state deduplication and group identity are caller assertions, not verified here.',
            'Energy sums must already include the intended robust-cost scaling and all state validity rules.',
            'This module does not fit parameters or include priors; only mask-independent priors can be added outside this solve.',
            'Supplied binary64 energies are numerical approximations; exact arithmetic does not certify continuous optical energies.',
            'The optimal-state DAG is not the full material/mask explanation ensemble or an uncertainty certificate.',
            'No semantic mask, material identification, product accuracy or AR acceptance is inferred.']}
    working = json.loads(_encoded(request))['blocks']
    grouped = {gid: [b for b in working if b['group_id'] == gid] for gid in request['minimum_counts']}
    impossible = [gid for gid, members in grouped.items()
                  if sum(max(s['count'] for s in b['states']) for b in members) < minimum_counts[gid]]
    if impossible:
        report.update(status='infeasible', complete=True, reason='insufficient_possible_group_support', infeasible_groups=impossible)
        return report
    budget = _Budget(policy)
    try:
        ratios = [s['energy'].as_integer_ratio() for b in working for s in b['states']]
        exponent = max(denominator.bit_length()-1 for _, denominator in ratios)
        if exponent+1 > policy.maximum_integer_bits:
            raise _WorkBudget('maximum_integer_bits')
        report['energy_lattice'] = {'quantum': '2**(-exponent)', 'exponent': exponent,
                                    'arithmetic': 'exact integer cross-products; no floating-point stop tolerance'}
        for state, (numerator, denominator) in zip((s for b in working for s in b['states']), ratios):
            shift = exponent-(denominator.bit_length()-1)
            if numerator and numerator.bit_length()+shift > policy.maximum_integer_bits:
                raise _WorkBudget('maximum_integer_bits')
            state['_units'] = budget.integer(numerator << shift)
        choices = tuple(min(b['states'], key=lambda s: (-s['count'], s['_units'], s['state_id']))['state_id'] for b in working)
        report['selected'], energy, count = _selected(working, choices, exponent, budget)
        for iteration in range(policy.maximum_iterations):
            divisor = math.gcd(energy, count)
            numerator, denominator = energy//divisor, count//divisor
            results = [_group_dp(members, minimum_counts[gid], numerator, denominator, budget, number)
                       for number, (gid, members) in enumerate(grouped.items())]
            reduced = budget.integer(sum(r['terminal']['score'] for r in results))
            if reduced > 0:
                raise RuntimeError('Dinkelbach reduced minimum must not exceed the current feasible ratio')
            choices = tuple(sid for r in results for sid in r['terminal']['chosen'])
            selected, next_energy, next_count = _selected(working, choices, exponent, budget)
            report['iterations'].append({'index': iteration, 'reference_energy_units': str(energy), 'reference_count': count,
                'reduced_cost_integer': str(reduced), 'reference_ratio_numerator_units': str(numerator),
                'reference_ratio_denominator': denominator, 'selected_count': next_count})
            report['selected'] = selected
            if reduced == 0:
                dag = _tie_dag(results, budget)
                report.update(status='optimal', complete=True, conditional_optimum_proven=True,
                              termination='exact_zero_reduced_cost', optimal_selection_dag=dag)
                break
            energy, count = next_energy, next_count
        else:
            report['reason'] = 'maximum_iterations'
    except _WorkBudget as error:
        report['reason'] = str(error)
    report['work'] = budget.counts
    report['work_scope'] = 'Cumulative DP nodes, retained/replacement edge insertions and transitions across all iterations; no hidden Cartesian enumeration'
    if report['status'] == 'incomplete' and report['selected'] is not None:
        report['selection_scope'] = 'Feasible conditional objective upper bound only; optimum not established within budget'
    return report
