# Topology generator rubric

`topology-generator-rubric.yaml` is the reusable review for the CycloneDX and C4 generators.

```bash
# score every system, save a report
uv run python compliance/scripts/evaluate_topology_diagrams.py --vault <vault-dir> --all --out report.json

# after changing a generator, fail on any regression
uv run python compliance/scripts/evaluate_topology_diagrams.py --vault <vault-dir> --all --baseline report.json

# write generated C4 blocks into each Diagrams note (idempotent)
uv run python compliance/scripts/evaluate_topology_diagrams.py --vault <vault-dir> --all --write-vault
```

Dimensions marked `review: human` are not computed; a reviewer fills them in per system
using the prompts in the YAML. Systems with no hand-drawn reference score only the
reference-free dimensions, and the report says how much of the rubric weight was scored.
