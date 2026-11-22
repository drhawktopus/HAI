# genetic_mesh — the research-informed inheritance mesh (in-tree subpackage)

An in-tree subpackage of the head, so it deploys with the head and is reachable in its container. It stays
*decoupled by being PURE* (below), not by living in a separate folder.

## What this is
The **bone-structure inheritance channel**: drive a child's 3D face *geometry* from genuine, research-informed
inheritance — separate from the coloring/soft-tissue channel that lives in the head's `genetics.py` prompt hand.

Two channels, because in reality they segregate independently (a child can take coloring from one parent and bone
structure from the other). This subsystem is channel (b); the prompt hand is (a).

## Architecture boundary (why it stays decoupled even though it's in-tree)
- **Pure functions over vertex arrays + region indices.** No FLAME model loading, no ComfyUI, no
  orchestrator imports. Input: `(N,3)` FLAME-frame verts + a `regions` dict of vertex-index arrays (the
  caller passes them). Output: transformed verts / fingerprint dicts. The VLM cross-check is domain-logic
  only (`structure_vlm.py`): prompt + parse + fuse, with the caller doing the actual HTTP call. This keeps
  the module independently testable and a clean boundary — the fold-in was physical, not logical.
- **FLAME frame:** +y up, +z out of the face, x = left↔right width.

## Stages
- **Stage 1 — gender dimorphism** — `dimorphism.apply_gender_dimorphism(verts, regions, gender)`. Mild jaw/
  brow/chin nudge. Gender reads mostly from texture, not geometry, so dimorphism is mild scaffolding.
- **Stage 2 — structural taxonomy + measurement** — `structure.measure_structure` (11-feature fingerprint,
  in-plane @0.9 / projection @0.4) + `structure_vlm.py` (VLM projection cross-check, fused @0.7).
- **Stage 3 — inheritance engine** — `inheritance.inherit_structure` (polygenic: per-feature parent lean
  softened by confidence + bounded variance; measured-parents only, NO heritage/continental prior).
- **Stage 4 — drive the mesh** — `structure_apply.apply_structural_hand` nudges the child mesh toward the
  dealt hand (jaw/cheek ops the lever probe proved render at production CN strength). Wired into
  `identity.simulate_children` (E1 step 2), fail-soft. Validated: renders visibly follow the hand.

## Grounding rules
Photo-first; appearance measured, never labelled; heritage informs inheritance *dynamics* only
(recessive-carrier priors — the part the photo is blind to); granular, never continental; never
classify race; honest that the research is plausible/directional, not clinical.
