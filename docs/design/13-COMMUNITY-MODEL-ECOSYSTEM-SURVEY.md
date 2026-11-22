Survey of `offlinecreator.com` (a Flux.1-era community model index attached to the LocalForge
AI local-runner product) for methods and weights applicable to P20, cross-checked against
HuggingFace and the BFL model card where it mattered. The site has zero Flux.2 coverage, so
its weights apply only to the Flux.1/Kontext path; its methods are architecture-independent.
All named artifacts are leads to verify, not confirmed facts (see §6).

## Architecture scope and method applicability

The referenced community site provides no coverage of Flux.2 models. Its corpus consists exclusively of Flux.1-era models.

Consequently:

*   **Methods** (inpainting, weight ladders, stacking, seed grids) apply to both Flux.1 and Flux.2 paths. These are architecture-independent techniques.
*   **Weights** (LoRAs/checkpoints listed in the source) apply only to the Flux.1 path via the Kontext harness. Flux.1 LoRAs cannot load on Flux.2 due to architectural differences (Flux.2 is 32B).
*   **Flux.2 32B QLoRA on 24 GB AMD/ROCm** is covered by internal documentation ([[Flux2-32B-QLoRA-AMD-ROCm-Windows]]). The abliterated Mistral encoder currently in use serves as the Flux.2-native analog to the LoRA-level fixes found in the source.

**Configuration**: Denoise 0.3–0.6; start at 0.3. Lower denoise values than those used on SDXL are required.

**Scope**: This method has not been tested in P20 measurements as of 2026-08-01.

## 2. Community-prior art and validated techniques

The following practices were derived independently during lab operations and converge with community standards.

| Community Standard | Lab Implementation / Convergence |
|---|---|
| **Multi-LoRA stacking**: Independent per-adapter strengths via chained loader nodes. | Lab `extra_loras` build implements this pattern. Validated design. |
## Named artifacts (Flux.1 ecosystem)

Artifact listings are community-sourced references for verification. Operators must hash files before use; mirrors may differ from source.

## Primary source verification for community models

Consult primary model cards and discussion threads to verify compatibility, trigger tokens, and licensing before deployment. Summaries may smooth over split results or missing documentation; primary sources provide definitive status.

### Kontext LoRA compatibility with Flux.1-dev

The transfer of Flux.1-dev LoRAs to Kontext is **unproven**. The BFL model-card discussion (`FLUX.1-Kontext-dev/discussions/19`) contains conflicting reports:
* `Viskirson`: Two dev LoRAs working, quality "pretty fine," with small test sample.
* `likewendy`: No work, error screenshot provided.

There is no official BFL response or documentation. Do not assume compatibility without individual testing.

Specific garment identification yields better results than generic instructions. 
* **Effective:** "Completely remove the subject's white crop-top and jean shorts"
* **Ineffective:** "Completely remove the subject's clothes" (may result in no changes)

Test specific phrasing immediately; it requires no additional weights, nodes, or downloads.

### Trigger token verification

Verify trigger tokens against the official model card. Typos in community mirrors cause silent failures.
* **Example:** `shahtab/FLUXNSFWunlock` (Apache-2.0, base `black-forest-labs/FLUX.1-dev`) lists trigger as `aidmaNSWFunlock` (F/W transposed), whereas CivitAI lists `aidmaNSFWunlock`.
* **Action:** Test both spellings when using this adapter to distinguish between token mismatch and adapter failure.

## Model Ecosystem Testing Protocol

Testing recommendations are prioritized by value-per-effort. All tests execute against the Kontext harness unless noted otherwise.

### R1.5: Instruction Phrasing Probe
Execute `probe_kontext_phrasing` to test garment-naming instructions versus generic instructions that produced unchanged images. This test has zero download cost. If results indicate success, subsequent recommendations (R2–R4) are suspended until this probe concludes.

### R1: Masked Inpainting Path
Build a masked-inpainting workflow as a sibling to `p20_kontext_edit.py`. ComfyUI nodes and existing render harness patterns provide the implementation base. This path sidesteps identity-preservation constraints associated with P20.
*   **Implementation**: Use mask input with `VAEEncodeForInpaint` and denoise control.
*   **Scope**: Test on both Kontext and Flux.2 paths. The method is architecture-independent despite weight differences.
*   **Design Question**: Determine mask source (hand-drawn per source vs. auto-derived via clothing segmentation model).

### R2: LoRA Support for Kontext Harness
Add LoRA support to `p20_kontext_edit.py` by inserting `LoraLoaderModelOnly` between `UNETLoader` and the guider, mirroring the existing SLIM chain. Test adapters in this order:
1.  `JD3GEN/JD3_Nudify_Kontext_LoRa`: Purpose-trained for Kontext. Requires the owner's HF license for gated repo access.
2.  `aidmaNSFWunlock`: Flux.1-dev based. Transfer validity is unproven per §4a. If used as fallback, test both trigger spellings defined in §4d.

### R5: Site as Reference Pointer
Treat community model sites as reference pointers, not authorities.
*   **Utility**: Provides specific filenames, weights, VRAM metrics, sampler recipes, and primary-source links.
*   **Limitations**: Lacks publication dates (potential staleness in fast-moving fields), lacks author/methodology attribution, relies on community anecdote, and includes the site's own products.
*   **Protocol**: Verify all pointers against primary sources on local hardware. Do not trust remote mirrors without hash verification.

## 6. Status of these findings

All named artifacts in §3 are unverified leads, not confirmed facts: IDs and sizes come from
an undated third-party page, and no file has been downloaded or hash-checked. The
Flux.1→Kontext transfer claim rests on a small forum sample, not documentation.
Recommendations R1–R4 are orders to write, not tested conclusions.
