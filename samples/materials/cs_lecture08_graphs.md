# CS1010 — Lecture 08: Graphs and Their Representations

## 8.1 Terminology
- Graph G = (V, E): vertices V, edges E.
- Undirected vs directed; simple vs multigraph.
- Degree of a vertex: number of incident edges (directed: in/out degree).
- Handshaking lemma: Σ deg(v) = 2|E|.

## 8.2 Representations
- Adjacency matrix: O(|V|²) space, O(1) edge lookup.
- Adjacency list: O(|V| + |E|) space, O(deg) neighbor iteration.
- Choice depends on density: dense → matrix, sparse → list.

## 8.3 Traversals (preview of lecture 09)
- BFS: queue-based, gives shortest path in unweighted graphs.
- DFS: stack/recursion-based, basis of cycle detection and topological sort.

## 8.4 Worked example
Graph: V={A,B,C,D}, E={(A,B),(B,C),(C,D),(A,D)}.
- Degrees: A=2, B=2, C=2, D=2 → Σ = 8 = 2|E| ✓
- BFS from A: A → B, D → C (shortest path A→C has length 2).

## Exercise
Draw the adjacency list of the graph above and run BFS from D.
