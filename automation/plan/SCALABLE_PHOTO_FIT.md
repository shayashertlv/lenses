# Conditional mask elimination for scalable photographic fitting

This is a **design proposal, not an implemented fitter**. It preserves the minimum
of the current training objective under stated constraints. It does not preserve
the current full ensemble of fitted mask branches, identify physical materials,
or establish product accuracy or AR acceptance. No existing thresholds change.

The standalone conditional selector is now implemented in
`reconstruction/conditional_mask_selection.py`. It accepts supplied disjoint
single-group block states and minimum counts; it does not build observation
blocks, evaluate optical energies, validate state metadata or integrate with a
fitter. Supplied finite binary64 energies are represented on an exact integer
lattice. Constrained DP/Dinkelbach termination and ties therefore use exact
integer comparisons for those numerical energies, with explicit integer, work,
iteration and tie-DAG budgets. Every alternative/provenance record is retained;
incomplete results never claim the conditional optimum.

Its 14 tests include 180 deterministic randomized comparisons against exhaustive
enumeration of both optimum values and every tied selection. A separate control
represents `10^20` optimal selections using 21 DAG nodes and 200 edges. Adjacent
binary64 energies, full binary64 dynamic range, subnormal ratio underflow and
integer/tie/work-budget exhaustion are covered. These are bounded software and
discrete-optimization tests, not evidence of material recovery or scalability of
the complete photographic fitting pipeline.

The current joint fitter enumerates one alternative per photo/group region.
With three alternatives, two photographs and two groups give `3^4 = 81` branches;
four photographs and two groups give `3^8 = 6,561`. Material/lighting/rear choices
and optimizer starts multiply those counts. The proposed elimination removes the
Cartesian product across independent mask blocks and photographs, while retaining
all supplied alternatives as evidence.

## The exact current objective

For channel `c` of a unique eligible training pixel `x` in photo `p`, define

```
e[x,c] = interval_residual(predicted_code[x,c], observed_code[x,c])
         / code_robust_scale
g(e)   = e * sqrt(2 / (sqrt(1 + e*e) + 1))
c[x]   = (1/6) * sum_c g(e[x,c])^2
       = (1/3) * sum_c (sqrt(1 + e[x,c]^2) - 1)
```

The interval residual retains the current one-sided censorship at observed code
0 and 255. It must not become ordinary RGB squared error or clipped predictions.

The existing residual weight for each copy of a pixel is
`1 / sqrt(3 * N[p] * multiplicity[x])`. SciPy's cost is half the squared residual
norm. Therefore duplicate copies cancel exactly and the photo contributes

```
L[p](theta, H) = sum_{x in U[p,H]} c[x](theta) / |U[p,H]|
J(theta, H)    = sum_p L[p](theta, H) + P(theta)
P(theta)      = (1/2) * ||material_priors(theta)||^2
              + (1/2) * ||shared_photo_priors(theta)||^2
```

`U` is the union of selected **unique training pixels**, not the sum of sample
counts. The current code sums equally weighted photo losses; it does not divide
that sum by the number of photos. Either losing the `1/6` factor or averaging
over photos would change the balance against the priors.

This deduplication assumes the existing strict consistency checks: repeated
source pixels have identical RGB/alpha, and repeated pixels within a group have
the same coordinates, directions, background and rear-content claims. Different
groups cannot simultaneously own an eligible pixel in the current observation
model. Contradictions must still reject input before optimization.

## Build disjoint blocks before fitting

Within each photo, make a graph whose vertices are region choices. Connect two
regions when the unions of their alternatives' eligible **training** pixels
overlap. Each connected component is a block. Enumerate combinations only inside
each block, retaining original hypothesis identities and numeric-alias receipts.

For each block state, deduplicate its selected pixels before calculating:

- `E[b,h](theta)`: sum of the unique-pixel costs above;
- `n[b,h]`: number of unique training pixels;
- `n[b,h,g]`: unique training counts for each material group;
- validity metadata and the selected hypothesis identities.

Different blocks then have disjoint training pixels, so their energies and
counts add. There is no claim of polynomial complexity for arbitrary inputs:
a large connected block can still have exponentially many states. Bound and
report that work explicitly instead of silently pruning alternatives.

## Conditional per-photo ratio minimization

With material, lighting and nuisance parameters `theta` fixed, and independent
admissible block states with a strictly positive total count, solve

```
rho[p](theta) = min_H  sum_b E[b,H[b]](theta) / sum_b n[b,H[b]]
F[p](lambda)  = sum_b min_h (E[b,h](theta) - lambda*n[b,h])
F[p](rho[p])  = 0
```

This is the finite fractional-programming/Dinkelbach construction. Starting from
a feasible state's ratio, minimize the reduced costs, then replace `lambda` with
the chosen state's total energy divided by its total count. Record the selected
states, denominator, reduced-cost residual, iteration budget and numerical stop
condition. Finite-precision termination is a numerical result, not an exact-real
certificate or a certificate of the outer material optimum.

Do not minimize each block's own mean independently. For example, one block has
states `(E,n)=(0.4,1)` and `(50,100)`, and another has fixed `(10,1)`. Independent
means prefer the first state's `0.4` over `0.5`, but the full photo ratios are
`10.4/2 = 5.2` versus `60/101`, about `0.594`.

## Support constraints change the inner solve

The current fitter requires a minimum training count for **every observed
group/photo pair**, not merely a positive photo denominator. An unconstrained
state `(E,n)=(0,1)` beats `(2,2)` but is invalid when the minimum is two. An
all-empty state `(0,0)` can also make the unconstrained root equation zero while
the ratio is undefined.

When support depends on several blocks, the correct reduced-cost function is

```
F[p](lambda) = min_{H satisfying all group/photo support constraints}
               sum_b (E[b,H[b]] - lambda*n[b,H[b]])
```

It is no longer a simple sum of independent minima. A bounded dynamic program
can process blocks while tracking a count vector for the photo's groups. Count
coordinates saturate at the required minimum `K[g]`: `min(K[g], accumulated[g])`.
Keep the lowest reduced cost for each count state and retain co-best provenance.
This is exact because blocks have disjoint training pixels. Denominators used
for the ratio remain the actual counts, not the saturated counts. For at most
four groups, the count-state budget is bounded by `product_g (K[g]+1)` before
further exact dominance reductions.

There is a stronger simplification under the current ownership contract: any
possible cross-group eligible-pixel overlap rejects the input before fitting.
Therefore each training-overlap block belongs to only one group. The support
constraint then separates by group, and each group's reduced-cost subproblem
needs only one saturated count coordinate, with `K[g]+1` states. Sum those group
minima to obtain the photo's constrained `F[p](lambda)`. The vector-count DP is
needed only for a broader future block/constraint contract; it is not necessary
to preserve the current ownership rule.

If each group's support is entirely inside one block, invalid local states can
instead be filtered directly. A sufficient fixed-support contract is a useful
first implementation, but it must be checked from the inputs and reported, not
assumed from the saved development products.

## Preserve branch-dependent model validity

Several existing rules inspect more than the training pixels:

- Smooth lighting requires finite reflected directions at **all eligible**
  selected pixels, including validation pixels.
- Rear activation is an OR over selected eligible pixels with positive rear
  weight. Geometry-conditioned rear color additionally requires all such colors
  to be known. These conditions span a group's photos.
- Cross-group ownership rejection currently covers all eligible pixels and all
  possible branch combinations, including validation-only collisions. Preserve
  it with union-based preflight; training-block separation is insufficient.
- Source bindings, coordinate/rear contradictions and image-grid consistency
  must be validated across all alternatives, including ones never selected by
  the conditional optimum.

Fix family assignment and lighting/rear model hypotheses in an outer structural
configuration. Block states carry the all-eligible validity flags for that
configuration. If a remaining flag requires a global existential condition,
track it explicitly or enumerate the bounded structural cases; do not claim
independent per-photo minimization while retaining an unmodeled global constraint.

A fixed superset of unknown-rear parameters can avoid changing parameter-vector
dimension. This preserves the minimum training value only if unused parameters
add no priors, retain the same bounds and explicit object-sharing aliases, and
have no effect at zero rear weight. Current rear variables have no regularizing
prior. Report unused coordinates as inactive/unidentified, and canonicalize a
mode with no rear support to its equivalent backdrop-only interpretation.
Adding a new penalty to those unused variables would change the objective.

The current valid group/photo graph is already invariant across admitted mask
branches: every region chooses one observation with the same photo/group, and
the positive minimum-support rule rejects a choice that removes any observed
edge. Consequently component membership and exposure/WB anchors can be built
once from that incidence graph, provided the exact support constraints remain.
If a future policy permits dropping a photo/group edge, the graph and anchor
choice can change; freezing the old gauge would then require a new explicit
model contract rather than being an equivalent optimization.

## Validation and optimization limits

Freeze the spatial split before mask elimination using the declared image size
or the union extent of **all** alternatives, as the current code does. Never
recompute it from selected masks. Select conditional states using training loss
and the existing metadata validity rules, not held-out RGB error or validation
coverage. Continue reporting every selected region's training/validation metrics;
a low pooled photo mean does not establish the current per-region photo policy.

States may have identical training pixels but different validation support.
Retain co-best aliases and their different support receipts. Choosing the one
with better validation error and then calling that error independent validation
would leak the held-out information into mask selection.

The outer objective is a lower envelope of branch objectives. At a unique active
state, its parameter gradient is the active branch gradient; state switches are
nonsmooth. Existing least-squares residual vectors change membership and length
with the mask and cannot simply be substituted into the current solver. A
bounded scalar-objective/active-set method, or alternating exact mask selection
and continuous fitting, needs its own convergence and replay checks. Alternating
steps can decrease the objective while remaining trapped in a local explanation.
Multiple starts and finite budgets do not prove a global optimum.

Most importantly, elimination preserves `min_H J(theta,H)` and, mathematically,
the minimum over `theta` if that outer problem could be solved globally. It does
**not** preserve every separately fitted branch, its local optimum, all
photo-policy-compatible alternatives, or the current explored AR-response
envelope. It is a scalable candidate-generation instrument. Uncertainty evidence
requires a separately declared procedure, such as retaining co-best or bounded
regret explanations with explicit coverage limits. No automatic material
identification or quality acceptance follows from this design.

## Bounded next-phase proof

Before changing the production fitter, use small synthetic multi-photo problems
where exhaustive enumeration remains possible. Compare the conditional optimum,
unique-pixel counts, support feasibility, active-state identities and total cost
including priors at fixed parameter vectors. Include overlapping same-group
regions, censored endpoints, empty/under-supported alternatives, all-eligible
direction/rear validity, validation-only ownership conflicts, disconnected graph
components and inactive shared rear variables. Check active-state gradients away
from ties and report ties explicitly. Then demonstrate increased photo count
without a global Cartesian expansion, while retaining the exhaustive solver as
the reference for these bounded cases.
