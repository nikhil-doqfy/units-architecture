# Review — Finance Spine Update (Parent Inheritance Reconciliation)

**Target:** `docs/finance/ARCHITECTURE-SPINE.md`, updated 2026-09-01 to inherit
`_bmad-output/planning-artifacts/architecture/architecture-units-architecture-2026-09-01/ARCHITECTURE-SPINE.md`
(Units Platform spine).

**Verdict: PASS**

No load-bearing defects found. All cited backend mechanisms (`jwt_token.py`,
`org_scope.get_pmc_ids_for_user()`, `helper_functions.prepare_response()`) were
verified to exist in `microservices/units-backend/utilities/` and match the
shapes the spine claims. The update is internally coherent, ratifies rather
than contradicts the parent, and genuinely narrows (rather than relocates)
the one Deferred ambiguity it touches.

---

## 1. Inherited Invariants table — ids and read-only status

**Pass.** The table (lines 20-28) uses exactly AD-1 through AD-5, unrenumbered,
matching the parent's own numbering 1:1. Cross-checked each row against the
parent spine:

| Parent AD | Parent concern | Finance's claim | Verified |
| --- | --- | --- | --- |
| AD-1 | Cross-app ORM access | "Ratifies Finance's own AD-2/AD-5 read-only ORM access — no conflict" | Correct — Finance's access is strictly read-only, a narrowing not a violation, of the parent's "MAY query directly" permission. |
| AD-2 | Access control enforcement via `get_pmc_ids_for_user()` | Routed to new AD-18 | Correct — AD-18 literally calls the same function the same way. |
| AD-3 | Response envelope `{content, message, status}` | Routed to amended AD-1 | Correct — see §2. |
| AD-4 | Role model (backend) | "No conflict — Finance's spine does not model roles" | Correct — Finance introduces no role/permission model; nothing in AD-1–AD-18 adds one. |
| AD-5 | Role-based access (frontend) | "No conflict — Finance's spine does not touch frontend structure" | Correct — Finance's spine has no frontend-structure content. |

No new or existing Finance AD (checked all 18) weakens or contradicts any of
these five. In particular, AD-9's Owner/Tenant handling isn't touched by
Finance at all (Finance has no role model), so parent AD-4's "unrestricted
within org scope, no ad hoc narrower gate without a new AD" rule is honored,
not overridden — and AD-18 explicitly defers the one place this could have
been violated (see §6).

## 2. AD-1 amendment coherence

**Pass.** AD-1's title changed to add "...response envelope matches the
inherited platform convention," but the rule body still opens with the
original framing — function-based `@api_view` only, no DRF class-based
views/serializers, hand-rolled `serialize_*()` functions. The added envelope
clause is layered on top, not a replacement: `serialize_*()` functions still
produce only the inner `content` payload; `prepare_response()` (imported,
not reinvented) does the wrapping. This is consistent, not contradictory —
"hand-rolled serialization" and "wrapped by the platform's own
`prepare_response()`" are compatible because the hand-rolled part is scoped
to shaping `content`, and the wrapping part is delegated to shared platform
code, exactly as the parent's own AD-3 does it for units-backend.

It does resolve the conflict it claims to: prior to this update, Finance's
AD-1 implied Finance would invent its own response shape (the Inherited
Invariants table's row for parent AD-3 says as much: "Every Finance endpoint
returns `prepare_response()`'s `{content, message, status}`"). The amendment
brings Finance's inner-`content` + `prepare_response()`-wrapper approach into
exact alignment with the verified parent implementation
(`utilities/helper_functions.py:42-59`, confirmed to return
`{"content", "message", "status"}` via `JsonResponse`).

## 3. AD-17 / AD-18 — necessity, completeness, no new conflicts

**Pass, both counts.**

**AD-17** (JWT auth for external reporting endpoints):
- Binds/Prevents/Rule are all present and complete.
- Independently necessary: without it, Finance's external endpoints have no
  defined authentication mechanism at all — AD-7 explicitly excludes itself
  ("distinct from AD-7, which governs only the internal
  units-backend→Finance sync path").
- Closes the named conflict (auth): confirmed `utilities/jwt_token.py`
  exists in units-backend, imports `pyjwt`, and implements
  `create_jwt_token()`/`get_jwt_token()` (Bearer-scheme parsing)/
  `decode_jwt_token()` — matching AD-17's claim exactly.
- No new conflict against parent AD-2 (authorization/PMC-scoping — a
  different concern than authentication), AD-4 (backend role model — AD-17
  doesn't touch roles), or AD-5 (frontend role gating — AD-17 is backend
  token validation, and its stated purpose is to make the existing
  `http.interceptor.ts` work unmodified, i.e. it supports AD-5 rather than
  touching it).

**AD-18** (PMC-scoping for reporting endpoints):
- Binds/Prevents/Rule complete; explicitly sequences itself ("applied
  before any Company-level scoping AD-2/AD-9 already do"), which avoids an
  ordering ambiguity a less careful AD might have left open.
- Independently necessary: without it, Finance's external endpoints would
  have no PMC boundary at all, despite living on the same platform where
  every other view enforces one.
- Closes the named conflict (PMC-scoping): confirmed
  `get_pmc_ids_for_user(user_profile)` exists at
  `utilities/org_scope.py:16` in units-backend, branching on
  PropertyManager/Owner/Tenant — matching AD-18's claim.
- No new conflict against parent AD-2 (AD-18 is a direct implementation of
  it, not a divergent one) or AD-4 (Owner/Tenant "unrestricted within org
  scope" — AD-18 stops exactly at org/PMC scope and explicitly declines to
  add a finer-grained gate, deferring that decision rather than smuggling
  it in as an ad hoc per-view check, which is precisely what parent AD-4
  requires: any narrower restriction "requires a new AD, not an ad hoc
  per-view check").

## 4. AD-7 vs AD-17 scoping boundary

**Pass, unambiguous.** AD-17's own binds clause states it explicitly:
"distinct from AD-7, which governs only the internal
units-backend→Finance sync path." AD-7's binds clause is equally explicit
("every endpoint under Finance's `/internal/` namespace"). The Consistency
Conventions table (State & cross-cutting row) separates the two under
distinct labels — "Auth (internal sync path): ... (AD-7)" vs. "Auth
(external reporting endpoints): ... (AD-17)" — leaving no endpoint able to
plausibly fall under both or neither. Verified independently that AD-7's
`X-Internal-Token`/`FINANCE_INTERNAL_TOKEN` pattern does not exist anywhere
in the current units-backend/units-frontend codebase — it's a Finance-local
invention for the internal sync path only, which matches its documented
scope and doesn't bleed into or get confused with AD-17's platform-shared
JWT mechanism.

## 5. Consistency Conventions / Capability→Architecture Map — staleness check

**Pass.** No leftover bracketed "[reconstruction note]" placeholders remain
in the Consistency Conventions table; both rows that touch the inherited
concerns now state the resolved convention plainly and cite the AD:
- Data & formats row: "Response envelope: `prepare_response()` →
  `{content, message, status}` for every endpoint, inherited from the
  platform spine's AD-3 (AD-1)."
- State & cross-cutting row: separately cites AD-7 (internal auth), AD-17
  (external JWT auth), and AD-18 (PMC access control), each attributed
  correctly.

The Capability → Architecture Map's Reporting row was updated to cite
AD-1, AD-16, AD-17, AD-18 — correctly picking up both new ADs where
relevant; no other row needed updating for this change (the internal-sync
row correctly still cites AD-7 only, not AD-17, since AD-17 doesn't apply
to that path).

The remaining bracketed text in the file (line 18's provenance note, line
104's "unresolved/placeholder naming question" inside AD-11's *Prevents*
clause describing what AD-11 itself prevents, and line 115's Django-EOL
corroboration note) are not leftover reconstruction placeholders — they are
intentional prose, correctly retained.

**Minor, non-blocking observation:** the top-level Invariants mermaid
diagram (`FE -- "HTTP GET ..." --> FN`) and the Structural Seed section
don't visually annotate the JWT/PMC-scoping now governing that edge. This is
cosmetic — the AD text and Consistency Conventions table fully and
unambiguously cover it — but a future pass could label that edge for
diagram/prose parity.

## 6. Deferred item update — does it genuinely narrow?

**Pass.** Before: the per-actor reporting visibility question was fully
open (both "who can see any of this" and "what detail within it"). After:
AD-18 closes the PMC-boundary question outright (verified against real
`get_pmc_ids_for_user()`), and the Deferred entry is rewritten to state
plainly what's now resolved and what specifically remains — "whether an
Owner's Trial Balance should additionally exclude PMC-internal commission
detail *within* a PMC they can already see." This is a strictly narrower,
well-bounded question (a single field-redaction decision), not a
relabeling of the same ambiguity. It also states the current default
behavior explicitly ("Phase 1 reporting endpoints are PMC-scoped (AD-18)
with no further role-based redaction beyond that"), so a builder reading
this Deferred item today knows exactly what ships in Phase 1 absent further
decision — it cannot be read two ways.

## 7. General spine health

- **Divergence risk in Deferred:** none found. Every Deferred item either
  states current Phase 1 behavior explicitly (commission redaction,
  reporting migration coexistence) or is genuinely out-of-phase-scope
  (multi-currency, Celery activation, Django 4.0.0 upgrade) — none leaves a
  load-bearing implementation choice unstated for two builders to guess at
  differently.
- **Stack/no new tech:** confirmed non-issue. AD-17 and AD-18 reference only
  pre-existing units-backend modules (`utilities.jwt_token`,
  `utilities.org_scope`); the Stack table is unmodified by this update and
  needed no changes.
- **Ratifies vs. contradicts parent:** confirmed throughout §1–§4 — every
  inherited AD is ratified or directly implemented, none weakened.

---

## Summary

| # | Check | Result |
| --- | --- | --- |
| 1 | Inherited Invariants — correct ids, read-only, no contradiction | Pass |
| 2 | AD-1 amendment coherent, resolves conflict, no self-contradiction | Pass |
| 3 | AD-17/AD-18 independently necessary, complete, no new conflicts | Pass |
| 4 | AD-7 vs AD-17 scoping unambiguous | Pass |
| 5 | No stale placeholders; map/table citations updated | Pass (minor diagram-labeling nit, non-blocking) |
| 6 | Deferred item genuinely narrows the open question | Pass |
| 7 | General spine health (divergence risk, stack, ratification) | Pass |
