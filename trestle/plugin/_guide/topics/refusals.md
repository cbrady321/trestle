# Publication refusal codes

A refused publication answers with `origin: "publication"` and one of these codes (no plugin is
published):

| Code | Fix |
|---|---|
| `publication.invalid_source` | the source does not parse |
| `publication.no_entrypoint` | add one `@trestle` function |
| `publication.name_mismatch` | `name` differs from the function's name: drop it or match it |
| `publication.source_too_large` | the source is over 512 KiB |
| `publication.validation_failed` | a hint, keyword, import or the import itself failed; the message says which |
| `publication.registry_conflict` | another server published the same name at once: retry |
| `publication.name_taken` | a pack plugin or the operator's own file has that name: read its source (the message gives the path) and publish your version under the suggested new name, with `adapted_from` |
| `publication.env_arg_missing` | declare `env_arg` (and match the root's `env_key_field`) |
| `publication.declaration_invalid` | the `WorkflowEntry` could not be read |
| `publication.unit_unresolved` | a child, `needs` or `gates` entry names an undeclared unit |
| `publication.dependency_cycle` | the `needs` edges form a cycle |
| `publication.declaration_conflict` | two declarations of one unit disagree |
| `publication.plan_precondition_uncovered` | a precondition no earlier node provides |
| `publication.plan_contract_missing` | a leaf lacks a required element |
| `publication.flags_contradict_type` | `LoopFlags` disagree with the declaration's type |
| `publication.safe_start_verb_invalid` | a safe-start verb other than start, refresh or install |
| `publication.facet_lifetime_mismatch` | an effect's lifetime does not suit its facet class |
| `publication.release_timeout_missing` | a run-lifetime create needs a release timeout |
| `publication.release_effect_once` | a release effect cannot be declared once |
| `publication.max_attempts_invalid` | `max_attempts` must be an int of at least 1 |
| `publication.recorded_with_remedies` | a recorded leaf cannot declare remedies |
| `publication.owned_remedy_on_found` | an owned remedy on a leaf that creates nothing |
| `publication.budget_exceeds_leaf` | wait, remedies and release timeout exceed the leaf's budget |
