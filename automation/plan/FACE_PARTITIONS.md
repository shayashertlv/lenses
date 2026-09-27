# Source components, face partitions and optical-group bindings

2026-09-22. These stages make within-primitive optical/frame/pad hypotheses representable without remeshing or losing source-face lineage. They do not infer which pieces are lenses. The [photo-semantic experiment](PHOTO_SEMANTIC_INFERENCE.md) explains why that decision cannot come from connectivity or a binary aperture mask alone.

## Two representations with different purposes

`reconstruction.mesh_components.component_face_labels` inventories connectivity without modifying geometry. It supports shared source vertex indices, exact local-position vertex contact, and exact local-position edge contact. Every source face receives a component label in first-source-face order, including duplicates and degenerates. Unused vertices do not create components. Position equality has no distance tolerance, and no geometry is welded or quantized. Point contacts and degenerate edges have explicit semantics; this is not a manifold or physical-identity test.

The inventory stage retains complete labels as NPZ arrays and records every selected-scene primitive instance, including ordinary fully alpha-zero primitives omitted by the existing silhouette reader. It preserves local positions/indices hashes, accessor references, world matrices and complete component counts/bounds. Components remain evidence arrays. Automatically emitting a primitive for every component would produce large draw-call and decoded-vertex costs without establishing physical groups.

`reconstruction.partition_glb.partition_glb_bytes` compiles **explicit, complete, disjoint source-face sets** into one primitive per declared piece. A later grouping algorithm can union component labels or cut through a connected component by supplying source-face ordinals. Omitted primitives remain unchanged. Duplicate triangles are distinguished by their original ordinal, so identical index triples do not hide a removed or repeated face occurrence.

## Preservation and limits

The exporter keeps the complete original BIN chunk as a prefix. Original node, mesh, accessor and buffer-view records stay at their original indices. Each new primitive reuses all original vertex attribute accessors and materials; only an unsigned index stream is appended. This preserves UVs, normals, tangents, normalized colors, custom attributes, sparse unrelated attributes, implicit-zero attributes, textures and source metadata without reinterpretation or baking.

The selected-scene hierarchy is cloned before mesh references change. Other scenes and shared source instances remain intact. Transforms, including reflected/nonuniform transforms, are retained without moving vertices or reversing source winding. All newly emitted corners still reference the same original vertex indices and attributes.

**Geometry/attribute preservation is not rendering equivalence.** Face order is retained within a piece and pieces are ordered by their earliest source face. Interleaved subsets can change global triangle sequence, and splitting primitives changes renderer sorting. Receipts explicitly report whether source triangle sequence changed and always leave rendering equivalence unmeasured. Transparent appearance needs separate validation after the intended physical groups/materials are compiled.

The static embedded-triangle profile rejects animation/skinning, morphs, compression, GPU instancing, unsupported structural extensions and canonical optical assets with existing source-bound group metadata. Partitioning must occur before optical compilation. POSITION and triangle indices use the existing ordinary-accessor profile; other attributes may use preserved normalized, sparse or implicit-zero storage. External images must first be embedded. Geometry/accessor/declaration capacity is checked before the corresponding large copies or membership allocations. Capacity failure does not silently drop components or faces.

Default bounds include 512 MB captured GLB bytes, four million source triangle occurrences, eight million decoded source vertex instances, and 4,096 output primitives. The component graph additionally bounds each primitive to two million vertices and one million faces, with 100,000 total recorded components at the inventory stage. These are resource limits, not a claim that an eight-million-vertex candidate meets the AR runtime's stricter limits. Sharing full original attribute accessors across split primitives can increase downstream decoding/export costs; the receipt reports that expanded inventory.

## Independent verification and recovery evidence

`verify_partition_glb_bytes` separately reads source/output bytes and checks the complete face lineage, actual appended indices, original document/BIN prefixes, node/mesh changes, all attribute references and all selected/inactive-scene bindings. It does not call the exporter. Rehashed contradictory receipts or outputs still fail. JSON preservation is type-sensitive: replacing numeric zero with `false` is not accepted as unchanged metadata.

The file stage captures immutable source and declaration snapshots, pins implementation/runtime, refuses an existing nonempty destination, and verifies before creating artifacts. Input/code/artifact mutation prevents a terminal report. These standalone stages have no resume/reuse path yet; an incomplete attempt must remain separate from a fresh output directory.

```powershell
python -m reconstruction.partition_stage inventory --model C:/path/source.glb --output data/components/example
python -m reconstruction.partition_stage partition --model C:/path/source.glb --declarations C:/path/partitions.json --output data/partitions/example
```

The declaration shape is:

```json
{
  "schema_version": 1,
  "source_sha256": "<exact source GLB SHA-256>",
  "provenance": {"method": "<source of this partition hypothesis>"},
  "partitions": [{
    "source_binding": {"node_index": 1, "mesh_index": 0, "primitive_index": 0},
    "pieces": [
      {"id": "piece-a", "source_face_indices": [0, 2]},
      {"id": "piece-b", "source_face_indices": [1, 3]}
    ]
  }]
}
```

The example assumes that primitive has exactly four faces. Each declared primitive needs full coverage; unmentioned faces cannot be silently retained under an unspecified semantic class. An unknown remainder must be an explicit piece. Piece IDs are globally unique and do not assert a semantic label.

## Bridge to the existing optical stages

`reconstruction.partition_optical_groups.declarations_for_partition` accepts explicit `{group_id, piece_ids}` hypotheses plus the source/output bytes and verified receipt. It resolves fresh partitioned-asset bindings and part indices, and returns the existing strict `run_optical_group_preparation` declaration schema. The original SHA, partitioned SHA and receipt SHA remain bound in provenance. Multiple pieces can share one effective optical group; separate groups remain separate.

Requested invisible pieces missing from the optical consumer's inventory cause an explicit failure, never incomplete membership. Unselected pieces retain their original material and unverified source role. In particular, splitting a pad out of a primitive named “lens” does not change inherited `partRole` metadata or prove the pad's material. The adapter intentionally requires explicit piece membership instead of rerunning source-role selection as a semantic decision.

Historical region reports cannot be rebound just by substituting the new asset SHA. Their old part indices no longer identify the partitioned primitives. Independent photo-semantic evidence needs an explicit source-face/visibility binding, while source-conditioned reports require verified translation or regeneration. This bridge is not yet connected to the job defaults or the optical observation stage.

## Validation scope

Focused tests cover independent connectivity oracles, shared scenes/instances, all face occurrences, sparse/normalized attributes, invisible primitives, declaration failures, allocation preflights, source mutation and rehashed receipt/output corruption. A controlled adapter test runs actual grouped preparation after partitioning. These checks establish a usable representation and binding path, not recovered identity or appearance.

The fixed corpus runner `qa.face_partition_corpus` applies the same deliberately nonsemantic policy to all five saved designs: split exact-position components by label parity, then preserve each original source-part optical hypothesis by grouping its resulting pieces together. This exercises arbitrary interleaved subsets and the actual preparation bridge without hand-authored product rules. It does not resolve the known whole-part lens/pad ambiguity. Its outcomes are recorded separately in the current progress report.

The next inference step must associate image-aperture hypotheses with these source pieces while retaining visibility, overlap/containment, competing groups and unknown assignments. That step—not partition export—must distinguish duplicated lens shells from pads, temples and separate lenses, and supply usable evidence for gradient/mirror fitting.
