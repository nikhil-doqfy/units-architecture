# Adversarial Review — Units Finance Module (Phase 1) Architecture Spine

**Target:** `docs/finance/ARCHITECTURE-SPINE.md` (updated 2026-09-01, inheriting parent AD-1..AD-5 from `_bmad-output/planning-artifacts/architecture/architecture-units-architecture-2026-09-01/ARCHITECTURE-SPINE.md`, amended Finance AD-1, added AD-17/AD-18)
**Method:** read the full spine + the parent platform spine + the actual `utilities/org_scope.py`, `utilities/helper_functions.py`, `utilities/jwt_token.py` implementations in `microservices/units-backend/`, since the spine's ADs bind Finance to *these specific functions*, not to an abstract description of them. `microservices/units-finance/` does not exist yet — this is a pre-build spine, so every scenario below is a real risk for the first two builders who pick it up, not a hypothetical about existing code.
**Verdict:** the spine reads as internally consistent on a single pass, but it is under-specified at exactly the seams where Finance (a new, foreign service) touches units-backend's identity/org model. Two compliant builders can diverge on all four prompted seams and on two additional ones found during review. The root cause is structural, not a wording nit: AD-17/AD-18 bind Finance to functions and conventions (`get_pmc_ids_for_user`, JWT, `prepare_response`) that were designed for and live inside units-backend's own process/ORM context, and the spine never states the adapter/glue code that lets a *separate* Django service call them meaningfully.

---

## 1. AD-7 vs AD-17 — auth mechanism seam

**Spine text:**
- AD-7: "every endpoint under Finance's `/internal/` namespace" requires `X-Internal-Token`.
- AD-17: binds "Finance's external reporting endpoints (Trial Balance, P&L, Balance Sheet, Ageing) — distinct from AD-7, which governs only the internal units-backend→Finance sync path."
- Structural Seed shows exactly one internal view (`views_internal.py`: `POST /internal/lease-transactions/{id}/sync`) and one reporting file (`views_reporting.py`: the four reports).

**Does the spine prevent confusion about which mechanism applies to which endpoint?**

Partially — the namespace convention (`/internal/` prefix, separate file `views_internal.py` vs `views_reporting.py`) is a reasonably strong tripwire, and AD-17's own text explicitly disambiguates from AD-7. For the endpoints named today, a builder would have to work hard to apply the wrong one.

**Where it breaks: an endpoint that plausibly needs both.**

The scenario the prompt flags is real and the spine has no answer for it. Two concrete candidates that satisfy the FRs but are unaddressed by any AD:

- **A preview/debug variant of the sync endpoint.** AD-6's retry logic and AD-14's idempotency-by-transition make "what would this LeaseTransaction post as, without actually posting" a natural operational need for support/reconciliation staff — e.g. `POST /internal/lease-transactions/{id}/sync?dry_run=true` or a `GET /internal/lease-transactions/{id}/preview`. If it's reached only by units-backend's signal handler, AD-7 applies cleanly. But the moment someone wants to trigger it from units-frontend (a "preview posting" button for an accountant, which is exactly the kind of feature a Phase-2 PRD item would ask for) it becomes user-facing and needs AD-17's JWT, while still living under the `/internal/` path namespace and still being conceptually "the sync endpoint." Builder A keeps it under `/internal/` and gates it with `X-Internal-Token` only (technically compliant with AD-7's namespace rule, silently unreachable from the frontend without also forwarding the shared secret to the browser — a red flag Builder A may not even notice). Builder B moves it to `/reporting/` or a new namespace and gates it with JWT only per AD-17, changing its URL contract and breaking any internal tooling that assumed the `/internal/` path. Both are spine-compliant; the resulting endpoints are incompatible.
- **A reporting endpoint that units-backend itself needs to call server-to-server** (e.g. a nightly reconciliation job in units-backend pulling Trial Balance to cross-check against its own rent-analytics numbers — explicitly left open by the spine's own "Reporting migration decision" Deferred item, PRD Open Question 10). That caller has no browser session and no user JWT to forward — it is a service-to-service call, structurally identical to AD-7's trust model, hitting an AD-17-governed endpoint. Nothing in the spine says whether server-to-server reporting calls (i) impersonate a user via a service-account JWT (undefined — units-backend's JWT is minted per-`UserProfile` via `create_jwt_token`, there is no service-account concept in `jwt_token.py`), (ii) get a second `X-Internal-Token`-gated route that bypasses AD-18's PMC scoping entirely (defeating the point of AD-18), or (iii) aren't supported at all yet the Deferred item implies they might be needed "in parallel." Two builders solving this independently will invent two different answers.

**Verdict:** confirmed. The named endpoints in the Structural Seed are unambiguous today, but the spine has no AD or convention line governing an endpoint that needs both mechanisms, and at least one such endpoint (preview/dry-run sync, or server-to-server reporting) is a near-certain Phase 1.5/2 ask given the adjacent FRs and Deferred items already on record.

---

## 2. AD-18's PMC-scoping vs Finance's own `Company` model — the missing mapping

**This is the single largest gap in the spine.**

**What AD-18 actually says:** "every reporting view calls `utilities.org_scope.get_pmc_ids_for_user()` ... and filters `Company`/`JournalEntry` results to the PMCs the requesting user can reach." It names `Company`/`JournalEntry` as the filter target and `get_pmc_ids_for_user()` as the filter source, and asserts the filter composes ("applied before any Company-level scoping AD-2/AD-9 already do").

**What `get_pmc_ids_for_user()` actually returns, read from source (`microservices/units-backend/utilities/org_scope.py`):** a `list[int]` of `PropertyManagmentCompany.id` — units-backend's own PMC model, resolved via `property.models.PMCPMMapping`/`Property.pmc_id`, keyed off a `user_profile` argument that must be an actual `PropertyManager`/`Owner`/`Tenant` row from units-backend's `user_service`/`property` apps (the function does `PropertyManager.objects.filter(pk=user_profile.pk)`, etc. — it needs a real row, not just an id).

**What Finance's own `Company` model is, per this spine's ERD (lines 195-201):** `id`, `name`, `base_currency`, `country`, `fiscal_year_start_month`. **There is no `pmc_id` field, no FK to `PropertyManagmentCompany`, and no field of any kind that names a PMC.** The only relationship the ERD draws to units-backend's org structure at all is `Unit ||--|| Company : "maps to (new Finance-owned table, FR-2)"` — a Unit-to-Company mapping, not a PMC-to-Company mapping.

So AD-18's rule — "filter `Company`/`JournalEntry` results to the PMCs the requesting user can reach" — requires a `Company -> PMC` path that literally does not exist in this spine's own data model. The only way to get from a `pmc_id` to a `Company` row is to compose two mappings that the spine never states together:

```
pmc_id --(units-backend: Property.pmc_id)--> Property --(units-backend FK chain)--> Unit --(Finance's new Unit->Company table)--> Company
```

This requires Finance to reach *back into units-backend's `Property`/`Unit` tables* (permitted under AD-2/AD-5's read-only cross-project ORM rule) to resolve which `Unit`s belong to a PMC, then join through the Finance-owned `Unit -> Company` table to get `Company` ids, then filter `JournalEntry.company_id__in=...`. That's a real, buildable query — but the spine names none of its steps, and the ERD's `Unit ||--|| Company` cardinality (one-to-one) plus the PRD's likely intent (a PMC manages many properties, each with many units, and Company is probably meant to represent a PMC's or Property's set of books, not one row per Unit) makes the exact granularity of that mapping genuinely ambiguous:

- **Is `Company` one row per PMC?** Then `Unit -> Company` would need to be many-to-one, not the `||--||` (one-to-one) the ERD literally draws, and the join is `Company.pmc_id` directly (if that field existed) — cheap and obvious.
- **Is `Company` one row per Property?** Then the join is `Company -> Property.pmc_id`, needing yet another FK not in the ERD.
- **Is `Company` one row per Unit** (matching the ERD's literal one-to-one), so a single PMC could own dozens of Finance `Company` rows, one per unit? That seems structurally odd for a Chart-of-Accounts-bearing entity (AD-11 gives each Company its own CoA fixture, AD-15/FR-9 do commission splits at the Company level) but is what the ERD actually draws.

None of AD-18, the Data & formats convention, or the Capability→Architecture Map row for "Reporting" resolves this. **Two builders each reading AD-18 literally can each write a technically-compliant PMC filter that returns different, incompatible result sets**, because "filter Company... to the PMCs the requesting user can reach" has no single unambiguous SQL translation given the schema as specified:

- Builder A assumes `Company` == PMC 1:1 in practice (Phase 1's UAE-only, small-scale reality might make this true incidentally) and adds an undocumented `Company.pmc_id` field never mentioned in the ERD, joining directly.
- Builder B takes the ERD literally, resolves PMC → Property → Unit → Company via the stated `Unit -> Company` mapping table, and never adds a `pmc_id` field to `Company` at all.

These two implementations have different migrations, different query plans, and — critically — will produce **different reporting results** the moment a PMC's properties/units don't cleanly funnel into a single Company (e.g. Phase 2 multi-property Companies, or Phase 1 edge cases where a Unit's owner changes PMC mid-lease). This is not a cosmetic divergence; it's a data-correctness divergence in a financial-reporting boundary condition.

**Verdict:** confirmed, and this is the most severe finding in the review. AD-18 names a rule ("filter by PMC-reachability") without naming a mechanism (the actual join path from `pmc_id` to `Company.id`), and the spine's own ERD doesn't carry the field that would make the join unambiguous. This should be a blocking gap, not a stylistic one — recommend the spine add an explicit AD (or amend AD-18) stating the exact FK/traversal path, ideally by adding a first-class `pmc_id` (or `Property`/PMC FK) directly onto Finance's `Company` model rather than requiring a multi-hop join through the Finance-owned `Unit -> Company` table at report-query time.

**Secondary problem inside the same AD:** `get_pmc_ids_for_user()` takes a `user_profile` object (a units-backend ORM row), not a `user_id` int. AD-17 authenticates the reporting request via a decoded JWT, and `decode_jwt_token()` (per `utilities/jwt_token.py`) yields a payload of exactly `{user_id, email, exp}` — no role, no `UserProfile` object. So a Finance view satisfying AD-17 has only a bare `user_id` in hand, but AD-18 requires calling a function whose first line is `PropertyManager.objects.filter(pk=user_profile.pk)...` — i.e. it needs a real `UserProfile`/`PropertyManager`/`Owner`/`Tenant` row. Finance must therefore ORM-fetch the `UserProfile` (and its role subclass) from the shared Postgres before calling `get_pmc_ids_for_user()` — permitted under the cross-project read-only rule, but **never stated**. Two builders will each write this glue differently: one fetches the correct role subclass properly; another takes a shortcut (e.g. constructs a lightweight duck-typed object with just `.pk` set, which would silently break `get_pmc_ids_for_user()`'s `select_related('company')` and role-specific branches, or picks the wrong role subclass by only checking `PropertyManager` and missing `Owner`/`Tenant`). This is exactly the "second, Finance-local reimplementation... diverging from units-backend's" failure mode the *parent* AD-2 explicitly warns against — yet the child spine's AD-18 doesn't specify the glue that would prevent it.

---

## 3. Parent AD-3 pagination vs Finance's reporting endpoints

**Parent AD-3 (inherited):** "A paginated endpoint MUST carry pagination via `prepare_response()`'s `paginator` argument — never nested inside `content`." Finance's amended AD-1 restates this: "A paginated reporting endpoint carries pagination via `prepare_response()`'s `paginator` argument, never nested inside `content`."

Note the phrasing carefully: both AD-3 and Finance's AD-1 say *"a paginated endpoint..."* — conditional. Neither AD says Trial Balance/P&L/Balance Sheet/Ageing **are** paginated endpoints, nor does either AD say they are **not**. The rule only governs *how* to paginate *if* an endpoint is paginated; it is silent on the antecedent question of whether these four specific endpoints should be.

**Are these naturally paginated collections or single computed documents?** Genuinely ambiguous by nature, and the FRs (FR-10 through FR-13, referenced but not quoted in this spine) aren't reproduced here to settle it:

- **Trial Balance / Balance Sheet** are conventionally *complete point-in-time snapshots* — every account with a non-zero balance, as of one date. A CFO expects to see the *whole* Trial Balance, not page 1 of 5. This argues strongly against pagination — it's a single computed document (JSON: array of account balances + a total).
- **P&L** is a period-bounded computed statement, same argument — single document, not naturally paginated.
- **Ageing** (AR/AP ageing) is the one report of the four that is plausibly a *list* rather than a *statement* — potentially one row per open invoice/cheque bucketed by age, which for a PMC with thousands of leases could genuinely be a long list that benefits from pagination the way any other list endpoint in units-backend would.

So even reasoning from first principles, three of the four reports lean "single document," one leans "list," and the spine adjudicates none of it. A builder implementing Trial Balance can legitimately decide either way and point to compliant AD text:

- **Builder A:** "AD-1/AD-3 only *govern* pagination when present; Trial Balance is a snapshot, not paginated at all — I return the full account list in `content`, no `paginator` argument, `pagination` key absent from the envelope." Fully compliant.
- **Builder B:** "PMC-scoped Trial Balance for a multi-PMC PropertyManager (AD-18) could return an unbounded number of accounts across many Companies; better safe than sorry — I apply Django's standard paginator and pass it through `prepare_response(paginator=...)`, same as every list endpoint in units-backend." Also fully compliant — nothing forbids paginating a document-shaped resource.

Both are individually "correct" reads of AD-1/AD-3/AD-18, and the frontend built against one behavior (raw array in `content`, no `pagination` key, always full data) will silently break — or silently truncate a report to page 1 with no visual indicator — against the other. This is worse than a stylistic inconsistency: for a *financial statement*, an accountant looking at a "Trial Balance" that's actually page 1 of 3 without realizing it is a correctness incident, not a UX quirk.

**Verdict:** confirmed. Recommend the spine add an explicit line (ideally per-endpoint) settling this: e.g. "Trial Balance, P&L, and Balance Sheet are single computed documents and are never paginated — `content` always carries the full result, `pagination` is absent. Ageing is a list endpoint and follows the standard paginated convention (AD-1/AD-3)." Absent that, this is a live fork point.

---

## 4. AD-1's error-path envelope shape

**What the amended AD-1 says:** "`serialize_*()` functions produce only the inner `content` payload; `prepare_response()` does the wrapping." This sentence is scoped to the *success* path — it describes what fills `content` when there's a resource to serialize. It says nothing about what an endpoint should put in `content`, `message`, or `status` when there's no resource — i.e., an error.

Checked `prepare_response()`'s actual signature (`microservices/units-backend/utilities/helper_functions.py`):

```python
def prepare_response(content={}, message='', status=status.HTTP_200_OK, paginator=None, total_records=0, pagination=None):
```

All three fields are independently-defaulted, untyped kwargs — the function itself enforces zero structure on what "an error response" looks like. It will just as happily produce `prepare_response(content={"error": "bad payload"}, message='', status=400)` as `prepare_response(content={}, message="Malformed request body", status=400)` as `prepare_response(content={"field_errors": {...}}, message="Validation failed", status=400)`. Nothing in units-backend's own convention (visible from this function alone) disambiguates this either — it's a bare utility, not a schema.

Concretely, for **Finance's internal sync endpoint rejecting a malformed post** (the prompt's example — this is `POST /internal/lease-transactions/{id}/sync`, AD-7-gated), two equally AD-1-compliant handlers:

- **Handler A:** puts the validation detail in `content` (treating `content` as "the payload, whatever shape it is, success or failure"), leaves `message` as a generic string like `"error"`, sets `status=400`. E.g. `prepare_response(content={"errors": ["missing lease_transaction_id"]}, message="error", status=400)`.
- **Handler B:** puts the human-readable detail in `message` (treating `message` as "the description of what happened," matching its name), leaves `content` empty (`{}`, matching "content is the resource, and there is no resource on failure"), sets `status=400`. E.g. `prepare_response(content={}, message="missing lease_transaction_id", status=400)`.

Both wrap via `prepare_response()`, both nominally satisfy AD-1's "every endpoint's HTTP response is wrapped in `prepare_response()`" and the amendment's "`serialize_*()` produce only `content`" (Handler B doesn't even call a `serialize_*()` on the error path, which is arguably *more* consistent with the amendment's letter — no resource, no serializer, empty content). Any caller (units-backend's retry-handling signal code, AD-6) that branches on response shape to decide whether a failure is retryable vs terminal, or that surfaces the error message to an ops log, will read a different field depending on which handler it's talking to — and since AD-6's retry logic lives in *units-backend*, calling into *Finance's* sync endpoint, this is a cross-service contract gap, not an internal Finance nit: units-backend's signal handler has to guess which shape Finance's error responses take, and nothing in AD-6, AD-7, or AD-1 pins it down.

There's a second-order version of this same gap worth flagging: `status` in `prepare_response()`'s signature is the **HTTP status code** (`status.HTTP_200_OK` etc. from DRF's `status` module), and it's *also* the literal key name in the `{content, message, status}` envelope per AD-1/parent-AD-3. That's a real, if minor, naming collision already latent in units-backend's own convention (not introduced by Finance) — but it means "check `resp.status`" is ambiguous between "the envelope's status field" and "the HTTP status of the response" in any error-handling code that talks about both, and a builder writing Finance's error paths inherits that ambiguity without any spine text calling it out.

**Verdict:** confirmed. Recommend an explicit convention line: for error responses, `content` is always `{}` (or a fixed error-detail shape, e.g. `{"errors": [...]}, consistently named and applied everywhere) and `message` always carries the human-readable failure reason — stated once, so AD-6's cross-service retry/logging code in units-backend has one shape to depend on rather than an assumption.

---

## 5. Additional divergence pairs found beyond the four prompted seams

### 5a. AD-9/AD-15's `ownership_percent` backfill timing vs AD-10/AD-12's independent deploy — a sequencing gap with no owning AD

AD-15 requires `UnitOwner.ownership_percent` to be `NOT NULL` with a mandatory backfill *in units-backend*, completed "before Finance goes live." AD-9 says Finance reads this field directly, read-only. But AD-10/AD-12 establish Finance as an independently-deployed sibling service with its own Dockerfile/compose block, and the Deferred section's `.env` item flags that `DB_HOST` rollout is "timed to happen once... not as an incidental side effect of an unrelated deploy" — i.e., the spine already knows deploy sequencing is a real hazard for *one* cutover step, but doesn't apply the same care to the AD-15 backfill migration's sequencing relative to Finance's first deploy/first posting run. Nothing states "the AD-15 migration must be applied and verified (all `Unit`s sum to 100%) before `units_finance`'s posting engine processes its first `LeaseTransaction`." Builder A (owning the units-backend migration) could deploy Finance first against a database where the backfill hasn't landed yet, hit AD-15's "fails loudly" clause on every multi-owner unit's first FR-9 commission split, and call it correctly-behaving ("the AD said fail loudly, so it's failing loudly, as designed") while Builder B (owning the Finance build) assumed the backfill was already a precondition satisfied by the time Finance goes live and never added an explicit health-check/guard for it. Both are individually AD-compliant; the combined system either mass-fails on day one or (worse) silently proceeds with a partial backfill if someone loosens the `NOT NULL` constraint under deploy pressure. This is a sequencing/runbook gap adjacent to the one the Deferred section already flags for `DB_HOST` — worth the same treatment (explicit runbook note), not silence.

### 5b. AD-16's "sum all rows unconditionally" vs AD-18's PMC-filtered `JournalEntry` queryset — composition order is asserted, not shown

AD-16 says every balance query "sums all `JournalEntry`/`LedgerLine` rows for the period unconditionally." AD-18 says PMC-scoping is "applied before any Company-level scoping AD-2/AD-9 already do" — establishing an order (PMC filter, then Company-level filter) but never stating where AD-16's "unconditional" full-period sum fits relative to *that* order. Read literally, "sums all... rows... unconditionally" and "filtered to the PMCs the requesting user can reach" are two constraints on the *same* queryset that must both hold, but the spine states them in two different ADs written for two different purposes (financial correctness vs access control) without ever showing them composed into one query. It's very likely fine in practice (`JournalEntry.objects.filter(company_id__in=pmc_scoped_companies, ...period...)` is the obvious answer), but "obvious" is exactly the word that predicts two builders reaching the same destination by different, subtly incompatible paths (e.g. one filters PMC at the `Company` queryset stage and joins, the other filters PMC as a post-hoc Python-side set intersection after fetching), especially given finding 2 above already shows the PMC→Company join path itself is unresolved. This compounds finding 2 rather than standing fully independent, but is worth naming as its own seam: **AD-16 and AD-18 are each unambiguous in isolation and have never been shown composed.**

---

## Summary Table

| # | Seam | Confirmed? | Severity |
|---|---|---|---|
| 1 | AD-7 (internal/shared-secret) vs AD-17 (external/JWT) — endpoint needing both | Confirmed | Medium — no endpoint today needs both, but a preview/dry-run sync or server-to-server reporting call is a near-term, foreseeable ask with zero governing text |
| 2 | AD-18 PMC-scoping vs Finance's own `Company` model — mapping path | Confirmed | **High** — no FK/join path from `pmc_id` to `Company.id` exists in the spine's own ERD; two builders will produce different, possibly incorrect, filtered result sets on a financial-reporting endpoint. Also: `get_pmc_ids_for_user()` needs a units-backend `UserProfile`-family ORM row, not the bare `user_id` a decoded JWT yields — the glue to bridge that is unstated |
| 3 | Parent AD-3 pagination vs Trial Balance/P&L/Balance Sheet/Ageing | Confirmed | Medium-High — silent truncation of a financial statement is a correctness incident, not a style nit |
| 4 | AD-1 error-path shape (`content` vs `message` vs `status`) | Confirmed | Medium — cross-service (units-backend's AD-6 retry/logging code depends on Finance's error shape) with no pinned contract |
| 5a | AD-15 backfill timing vs Finance's independent deploy (AD-10/AD-12) | New | Medium — deploy-sequencing hazard, same category the spine already flags for `DB_HOST` but doesn't extend here |
| 5b | AD-16 "unconditional" sum vs AD-18 PMC filter — composition never shown | New | Low-Medium — likely resolves the same way in practice, but rides on the same unresolved join path as finding 2 |

**Bottom line:** the spine is well-structured and its own internal cross-references (AD-17 explicitly citing AD-7, AD-18 explicitly citing AD-2/AD-9, the amended AD-1 explicitly citing the parent AD-3) show real effort to reconcile the inherited invariants with Finance's pre-existing 16 ADs. But inheritance reconciliation stopped at the *policy* layer (which function to call, which envelope shape) without reaching the *mechanism* layer (what object that function needs, what field enables that filter, what shape an error takes) at exactly the two points where Finance is not units-backend — its own `Company` model and its own error paths. Recommend closing finding 2 (the PMC→Company mapping) as a blocking amendment before build starts; the rest are worth one clarifying line each in the spine or Consistency Conventions table.
