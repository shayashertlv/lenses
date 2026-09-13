# Review compositor candidate

`reuse-compose` is a separate option implementing item 4 of the September 13
review. X already compares the initial RGBA residuals as words. This candidate
also restricts detailed membership/category/feather work to conservative row
spans containing every saved editable, optical and nasal rectangle, and reuses
output allocations through explicit ownership leases.

Every source/background pixel still receives an exact four-channel residual
check. Every final pixel still receives the existing independent nose, optical
front, outside-editable and clean-background audit, including after continuity.
Pixel resolution, mask sampling, feather arithmetic, validation ordering and
accepted-pixel restoration remain the same. Unaligned/shared source views use
exact byte comparisons with a declared fallback.

The output pool holds at most two buffers. A retained result owns its lease and
cannot be overwritten. A third simultaneous owner receives an independent
allocation. Hold/export synchronously encode or independently copy results;
the renderer releases its lease only after dropping its previous ImageData.
Cancellation, rejection, clear/restart and disposal release or detach ownership.
Callers needing raw pixels beyond release must first call `copyPixels()`.

`candidatePerformance.reviewCompose` records actual residual, detailed and
background scan counts, the full final-audit count, output allocation/reuse,
initial full-image copy size, span allocation/reuse and fallback reasons. The
initial copy count excludes authoritative guard copies; reusing an allocation
does not remove the initial pixel copy. These counters establish execution of
the mechanism. They do not establish a throughput gain.

The focused tests compare exact output, weights, statistics, validation and
eligible indices against both the earlier fast path and the independent frozen
composer. They cover both model contracts, category edges, alpha, malformed
pairing, protected/outside/background corruption, resolution changes and retained
output ownership. Renderer tests exercise real lease use through publication,
Hold/export, failure and disposal with stubbed native rendering.

The optional matched matrix accepts `--profile=reuse-compose` and independently
checks all archived poses, both glasses and both hair models. Its Python PNG
audit reconstructs span coverage and actual allocation/reuse from saved raw
evidence. Recorded/generated stills are bounded evidence; physical iPhone speed,
wearer motion and visual acceptance require the owner's preview run.
