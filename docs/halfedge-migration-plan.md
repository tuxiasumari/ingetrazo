# Topology engine migration: shared-vertex, non-manifold connectivity

> Status: **in progress** — M0 and M1 done.
> Written in English per the repo convention (code/docs/comments in English).

## Why

The current engine (`core/geometry.py`, `core/topology.py`) stores **copies** of
points in every `Edge`/`Face` and **rediscovers connectivity** on each operation
by matching rounded positions (`_key`, 4 decimals). It is capable and well-tested
(124 tests) but has a structural ceiling:

- **Tolerance fragility.** Connectivity-by-position is sensitive to float
  precision — `QVector3D` stores float32, so `0.3` becomes `0.30000001` and a
  `1e-9` comparison breaks (hit in practice while testing through-holes).
- **Push/pull as a case tree.** `tools/pushpull.py::_commit` has grown into a
  ~5-branch decision tree (prism-cap / through / attached+perp / attached+coplanar
  / free). Each new scenario adds a branch — the pattern that gets fragile.
- **O(n) rediscovery.** Move (`translate_points`) scans every edge and face each
  drag frame, moving those whose position key matches.
- **Sticky everything.** Auto-merge welds all touching geometry, so there is no
  notion of an isolated object → Groups are forced.

## What SketchUp does (validates the target)

SketchUp is closed-source, but its API/behavior expose its model:

- **`Vertex`** — first-class, **shared** (a corner is one vertex, not copies).
- **`Edge`** — connects two vertices and knows its incident faces; `edge.faces`
  is an **array of 0, 1, 2, or more** — i.e. **non-manifold** (three faces meet
  where two walls and a floor join, and SketchUp handles it natively).
- **`Face`** — bounded by loops of edges, with inner loops for holes
  (windows/doors).
- **"Sticky geometry"** (auto-merge) + **Groups/Components** to isolate from it.

So SketchUp uses a **shared-vertex, non-manifold B-rep** — *not* a textbook
(manifold) half-edge. IngeTrazo arrived at the same conceptual model by
dogfooding; the difference is purely implementation (rediscovered vs persistent
connectivity). The migration target below is essentially SketchUp's model.

## Target model (Level B + C), not textbook half-edge

A spectrum from A (today) to E (full kernel). The sweet spot:

- **Level B — Shared vertices.** `Vertex` is a first-class object; `Edge`
  references two `Vertex`; `Face` is a loop of shared `Vertex`. Moving a vertex
  moves every edge/face referencing it *for free* — no matching, no float
  tolerance. The biggest-leverage change, and the foundation Groups needs.
- **Level C — Edge↔face incidence (radial).** Each `Edge` knows its incident
  faces (a list, possibly >2 → non-manifold OK). Push/pull becomes a single
  sweep operation over incidence instead of a case tree.
- **D (manifold half-edge) and E (full radial-edge B-rep)** — wrong / overkill.
  Manifold half-edge would break on the project's own architectural geometry.

### What it solves / does not solve

Solves: tolerance bugs on **already-connected** geometry (move/push/deform);
O(1) move per vertex; push/pull as a general operation; a natural basis for
Groups; index-based serialization (OBJ/glTF/IFC-friendly).

Does **not** solve by itself: robustness of **new** intersections (a drawn line
crossing existing geometry still needs a computed, tolerance-bound split —
half-edge does not give booleans); the render pick `O(n)` (that is a spatial
index, separate); it also requires choosing a **non-manifold policy**.

## Blast radius

`core/geometry.py` (new `Vertex`, refs), `core/scene.py` (vertex registry),
`core/topology.py` (most `_key` matching simplifies/disappears),
`core/edits.py` (planner stops simulating by position), `core/history.py`
(commands by reference), `core/triangulate.py` (minor input adaptation),
`tools/pushpull.py` (push/pull rewritten over incidence), `views/viewport.py`
(render reads `Vertex.position`), `formats/igz.py` (indexed schema + `.igz`
migration), `tests/` (many rewritten). Roughly half the core plus tests.

## Migration strategy — incremental behind a facade (never a big bang)

The app must never be broken for more than one session; each phase is committed
and runnable.

- **M0 — New model in parallel.** `Vertex` + `Mesh` (`core/mesh.py`) with their
  own tests. The app is untouched and keeps using the legacy model.
- **M1 — Compatibility layer.** `Mesh` exposes `.edges`/`.faces` in today's
  shape (read-only view) so render and tools run unchanged.
- **M2 — Migrate mutations one at a time** behind the `Command` facade:
  add edge/face/weld → split → move → push/pull. Each step: green tests + running
  app.
- **M3 — Delete the legacy model + `_key` matching** once nothing uses it.
- **M4 — New serialization + `.igz` migration.**

## Effort (focus sessions, no visible features — pure foundation)

- Level B (shared vertices: M0+M1 + migrate add/move): ~3–4 sessions.
- Level C (incidence + push/pull rewrite): ~4–6 sessions.
- M3+M4 (cleanup + format): ~1–2 sessions.
- Full B→C: ~8–11 sessions.

## Decision

Start with **Level B incremental, now**: it fixes the concrete pains already
hit, is the foundation Groups needs anyway, is the cheapest/lowest-risk third of
the full refactor, and leaves Level C open without commitment.

## Progress log

- **M0 (done):** `core/mesh.py` — `Vertex` (shared), `Edge` (radial face
  list), `Face` (vertex loops + holes), `Mesh` (vertex registry with weld-on-
  insert, edge dedup, face boundary-edge creation, incidence maintenance,
  `move_vertex`). Tests in `tests/test_mesh.py`. App untouched.
- **M1 (done):** `mesh.Face` exposes `.vertices` / `.holes` as positions
  (storage stays vertex loops `loop` / `hole_loops`), so a `Mesh` is read-
  compatible with the legacy consumers. Proven by feeding a `Mesh` straight to
  `formats.igz.save_scene` (the real save path reads `.edges`→`.a/.b` and
  `.faces`→`.vertices/.holes/.triangulate`). App still untouched.
- **M2 (started):** mutation operations on the Mesh, built and tested in
  parallel before the live Scene/Command swap.
  - `mesh.split_edge(edge, position)` — splits an edge and inserts the shared
    vertex into *every* incident face loop (any count → non-manifold), in one
    pass. The legacy model needed position-matching + `split_edge_in_faces` +
    the holes patch + hit the float32 bug for this; here it falls out of shared
    connectivity. Tests cover two-face, three-face (non-manifold), and
    split-then-move (gable) cases.
  - **Next:** make `Scene` wrap a `Mesh`, then migrate the Commands
    (Add/Delete Edge/Face, MoveVertices, PruneOrphans) to mutate it — the
    coordinated swap that flips the live app, gated by the full test suite.
