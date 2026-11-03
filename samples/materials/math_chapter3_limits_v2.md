# MATH2001 — Chapter 3: Limits and Continuity (Lecture Notes v2 — UPDATED)

> v2 (supersedes v1): added section 3.5 and corrected the IVT statement.

## 3.1 Intuitive notion
A function f(x) approaches L as x approaches a if values of f(x) get
arbitrarily close to L for x sufficiently close to a (x ≠ a).

## 3.2 One-sided limits
- Left-hand limit:  lim x→a⁻ f(x)
- Right-hand limit: lim x→a⁺ f(x)
- The two-sided limit exists iff both one-sided limits exist and are equal.

## 3.3 Continuity
f is continuous at a iff:
1. f(a) is defined
2. lim x→a f(x) exists
3. lim x→a f(x) = f(a)

Types of discontinuity: removable, jump, infinite, oscillating.

## 3.4 Key theorems (week 4)
- Intermediate Value Theorem (IVT): if f is continuous on [a,b] and k lies
  between f(a) and f(b), then ∃c ∈ (a,b) with f(c) = k. *(corrected)*
- Boundedness on closed intervals.

## 3.5 NEW in v2 — Limit laws
lim (f ± g) = lim f ± lim g, provided both limits exist.
lim (f·g) = lim f · lim g.  lim (f/g) = (lim f)/(lim g) when lim g ≠ 0.

## Common mistakes
- Plugging x = a into the limit (only valid when f is continuous at a).
- Confusing "close to a" with "equal to a".
- Forgetting the two-sided limit: one side existing is not enough.
