---
name: c4-mermaid
description: Write, generate, review and validate C4 diagrams in Mermaid (C4Context, C4Container, C4Component, C4Dynamic, C4Deployment). Use when drawing or reviewing system context or container views, comparing a hand-drawn C4 with one generated from a Ports/Protocols/Services table, or fixing a C4 block that will not render or is unreadable. Covers which C4 level to use, the exact Mermaid syntax and its traps, validation, and layout.
---

# C4 in Mermaid

Source: <https://mermaid.ai/open-source/syntax/c4.html>. The syntax is marked **experimental**
by Mermaid and is compatible with C4-PlantUML. Everything below marked *(verified)* was
checked against Mermaid 12.2.0; everything else is from the docs. Re-check on upgrade.

## 1. Pick the level first

| Mermaid keyword | C4 level | Shows | Use it for |
|---|---|---|---|
| `C4Context` | 1 System Context | The system as one box, people and external systems around it | Authorization-boundary overview, "who/what talks to us" |
| `C4Container` | 2 Container | Runnable units and data stores inside the system, grouped by boundary | Architecture of what is deployed |
| `C4Component` | 3 Component | Parts inside one container | Only when you have code-level knowledge |
| `C4Dynamic` | (supplementary) | Numbered interaction sequence | One scenario, e.g. a login |
| `C4Deployment` | (supplementary) | Nodes the containers run on | Hosting and environments |

Rules that keep a diagram honest:
- A Ports/Protocols/Services (PPS) table supports **Context and Container only**. Do not
  produce Component or Code views from it; there is no evidence for them.
- Do not put containers in a `C4Context`, or people inside a container's internals. It renders
  *(verified)*, but a reviewer will read it as a level mistake.
- One level per diagram. Aggregate at Context (one relationship per counterpart); list every
  flow at Container.
- C4 is about structure. Trust boundaries, encryption and data classification belong on the
  data-flow diagram; show only protocol/port on a C4 edge.

## 2. Syntax

```text
C4Container
  title <text>
  Person(alias, "Label", "Description")
  Person_Ext(alias, "Label")
  System(alias, "Label", "Description")        System_Ext  SystemDb  SystemQueue  (+ _Ext)
  Container(alias, "Label", "Technology", "Description")
  ContainerDb / ContainerQueue / Container_Ext      (same arguments)
  Component(alias, "Label", "Technology", "Description")
  System_Boundary(alias, "Label") { ... }     Enterprise_Boundary  Container_Boundary  Boundary(alias,"Label","type")
  Deployment_Node(alias, "Label", "Type") { ... }   Node  Node_L  Node_R
  Rel(from, to, "Label", "Technology")        BiRel  Rel_U/D/L/R  Rel_Back  RelIndex(n, from, to, "Label")
  UpdateElementStyle(alias, $fontColor="white", $bgColor="#08427B", $borderColor="#08427B")
  UpdateRelStyle(from, to, $textColor="red", $lineColor="blue", $offsetX="-40", $offsetY="60")
  UpdateLayoutConfig($c4ShapeInRow="4", $c4BoundaryInRow="1")
```

Argument order differs by element: `Container(alias, label, techn, descr)` but
`System(alias, label, descr)`. Optional arguments can be named with a `$` prefix:
`Container(a, "A", $techn="Flask", $descr="API")`.

The `RelIndex` index argument is ignored (sequence follows statement order). `Lay_U/D/L/R`
layout statements are **not** supported. Sprites, tags, links and legends are listed as
unsupported in the docs.

## 3. Traps *(verified on 12.2.0)*

| Input | Result | Do this |
|---|---|---|
| `Rel(a, ghost, ...)` where `ghost` was never declared | `mermaid.parse` **accepts** it; a full render **fails** ("references an unknown shape or boundary") | Check aliases yourself before publishing (see Validation) |
| Same alias declared twice | Accepted; **the second silently replaces the first** | Keep aliases unique |
| `System_Boundary(b, "B")` with no `{` ... `}` followed by elements | Parse error | Always use braces |
| Missing closing `}` | Parse error | Balance braces |
| Header in lower case (`c4container`) | "No diagram type detected" | Use the exact keyword |
| `"` inside a label | Renders but mangles the label (`S \`) | Use `'` or drop it |
| Hyphen in alias (`my-api`) | Renders | Prefer `_`; hyphens are not in the documented grammar |
| Empty technology `Container(a,"A","")` | Renders cleanly | **Leave it empty rather than guess** a stack |
| No `title` | Renders without a title | Always give one |
| A line starting `%%` | Comment, ignored | Fine for notes |
| `<br/>` in a description | Line break | Fine |

Wrapping is on by default since 11.17.1; disable with front matter `config: c4: wrap: false`.

## 4. Layout: there is no auto-layout

Shape position follows **statement order**. Tuning, in order of effect:
1. **Declare components that talk to each other next to each other.** The single biggest lever.
2. `UpdateLayoutConfig($c4ShapeInRow=..., $c4BoundaryInRow=...)`. Defaults are 4 and 2. A wide
   single column of boundaries (`$c4BoundaryInRow="1"`) with 4 shapes per row was the most
   readable for a ~20 element system *(checked visually)*.
3. Keep edge labels short (about 60 characters). Long labels collide; Mermaid does not route
   around them. Put detail in the technology field or in a table beside the diagram.
4. `UpdateRelStyle(..., $offsetX, $offsetY)` nudges one label by hand. Use sparingly; it is
   brittle when elements move.
5. Past about 20 elements or 8 edges on one node, split the diagram (per application, or
   Context plus one Container view per boundary) instead of tuning.

## 5. Validation

Two different checks; do both.
1. **Grammar**: `node skills/c4-mermaid/scripts/validate.mjs file.mmd notes.md`. Needs
   `npm i mermaid@12 jsdom` in a scratch directory. Grammar only (see its header).
2. **Structure**: `validate_generated_c4()` in `compliance/lib/topology_eval.py` reports unknown
   aliases, duplicate aliases, unclosed boundaries, missing title and orphan elements.
3. Optional: render in a real browser (Playwright + `mermaid.min.js`) and screenshot. This is
   the only check that catches alias errors the way Mermaid itself does, and the only way to
   see label collisions.

## 6. Reviewing a C4 (checklist)

- [ ] Right level; one level per diagram.
- [ ] Every element has a type word and a name a stakeholder recognises (not a table-cell
      artefact such as "Y" or "Reference Figure 9-1").
- [ ] Every `Rel` names a protocol/port in the technology field and a purpose in the label.
- [ ] Nothing invented: unknown technology is blank; intermediaries (proxies, routers) appear
      only if the source states them.
- [ ] External systems are `_Ext`; people are `Person`.
- [ ] Boundaries match a real ownership/deployment boundary, not just a visual grouping.
- [ ] Renders in Mermaid 12; no label collides with a box.
- [ ] If a data-flow diagram exists for the same system: every boundary-crossing flow there has
      a matching relationship here.

## 7. Generating from a PPS table

`compliance/lib/topology.py` parses the table, `topology_c4.py` renders Context and Container,
`topology_vault.py` writes the result between `<!-- generated:<id> begin/end -->` markers in a
vault Diagrams note without touching hand-written content, and
`compliance/scripts/evaluate_topology_diagrams.py` scores generated against hand-drawn with
`docs/rubrics/topology-generator-rubric.yaml`. Known limit: intermediaries mentioned only in a
row's purpose text ("via Caddy Proxy") and components only described in SSP prose are not in
the table, so the generated view is a floor, not a complete picture.
