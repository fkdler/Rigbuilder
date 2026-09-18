# Truth Bundle V3.2 data area

- `schemas/` contains the generated, committed V3.2 JSON Schemas.
- `drafts/legacy/` contains the 22 hardware, 8 model (with 24 embedded variants), 174 legacy Evidence inputs and conversion report. Every Bundle is `pending`; none is importable.
- A production release must live outside `drafts/`, contain only human-reviewed `accepted` Bundles, and have a hash-pinned `manifest.json` produced by `backend/scripts/build_v3_manifest.py`.

No production Release is fabricated in this repository. Remote cutover must not run until an actually reviewed Release is supplied alongside the matching code commit.


## 2026-09-15 reviewed model capability release

`releases/rigbuilder-v3-4-model-capabilities-2026-09-15` contains 44 newly reviewed model metadata records and capability evidence for one existing model. Its 94 bundles passed validation, dry-run and the local transactional import. The original drafts remain pending; their unverified fields were not blanket-approved. See `reports/model-capability-review-2026-09-15.json` for dispositions and `reports/model-capability-apply.json` for import results.

Capability evidence is based on actually retrieved official repository task metadata; it does not certify quantized artifacts, local runtime compatibility or benchmark quality. Fields omitted during review are unknown. The model response must distinguish these facts from deployment advice.
