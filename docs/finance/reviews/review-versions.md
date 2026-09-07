# Review: ARCHITECTURE-SPINE.md reconciliation (2026-09-01 update)

**Scope:** Verify that the 2026-09-01 update to `docs/finance/ARCHITECTURE-SPINE.md` (Inherited Invariants section, amended AD-1, new AD-17/AD-18) did not introduce any unverified technology/library/version claim, and that the three functions/modules it now cites as binding conventions genuinely exist and behave as described in `microservices/units-backend`.

**Verdict: PARTIAL FAIL.** No new/unverified technology or version claims were introduced by this update — every function/module cited genuinely exists and matches its described behavior. However, **AD-17's core premise is false**: Finance cannot validate units-backend's JWT using "the same secret ... shared via the same root `.env`," because units-backend's JWT secret is not sourced from `.env` at all — it is `settings.SECRET_KEY`, a hardcoded literal string in `property_management/settings.py`, and the root `.env` contains no `SECRET_KEY`/`JWT_SECRET_KEY` entry whatsoever. AD-17 as written is not satisfiable by the mechanism it names.

---

## 1. `utilities/helper_functions.py` — `prepare_response()`

**File:** `/home/rahul/Optiex/Docufy/Code/units-architecture/microservices/units-backend/utilities/helper_functions.py`, lines 42-59.

Confirmed to exist and confirmed to return the claimed shape:

```python
def prepare_response(content={}, message='', status=status.HTTP_200_OK, paginator=None, total_records=0,pagination=None):
    resp = {
        "content": content,
        "message": message,
        "status" : status
    }
    if pagination:
        resp["pagination"] = pagination
    if paginator:
        resp['pagination'] = { ... }
    return JsonResponse(resp, status=status)
```

- Returns exactly `{content, message, status}` as the spine claims (AD-1's amended text, Data & formats convention row).
- The spine's specific extra claim — "A paginated reporting endpoint carries pagination via `prepare_response()`'s `paginator` argument, never nested inside `content`" — is also verified: pagination is injected as a top-level `resp['pagination']` key, sibling to `content`, not nested inside it. **Verified.**

## 2. `utilities/org_scope.py` — `get_pmc_ids_for_user()`

**File:** `/home/rahul/Optiex/Docufy/Code/units-architecture/microservices/units-backend/utilities/org_scope.py`, lines 16-58.

Confirmed to exist, with signature `get_pmc_ids_for_user(user_profile) -> list[int]`, branching on `PropertyManager` / `Owner` / `Tenant` and resolving PMC ids accordingly. Matches AD-18's and the Inherited-Invariants table's description ("filters `Company`/`JournalEntry` results to the PMCs the requesting user can reach"). The module docstring itself documents the intended calling convention ("Call this once per request; pass the result into every queryset filter"), consistent with AD-18's "the same function, called the same way units-backend does." **Verified.**

## 3. `utilities/jwt_token.py` — JWT creation/decoding via pyjwt, and the AD-17 shared-secret claim

**File:** `/home/rahul/Optiex/Docufy/Code/units-architecture/microservices/units-backend/utilities/jwt_token.py`.

- `import jwt` (PyJWT) at line 1 — confirmed the installed/pinned library is PyJWT (`pip show pyjwt` → `Name: PyJWT`, version 2.10.1; also pinned literally as `PyJWT` in `docker_config/python_config/requirements.txt` line 14). Matches AD-17's "`pyjwt`" claim. **Verified.**
- `create_jwt_token()` (lines 7-17) calls `jwt.encode(payload, JWT_SECRET_KEY, algorithm=JWT_ALGORITHM)` and `decode_jwt_token()` (lines 27-34) calls `jwt.decode(token, JWT_SECRET_KEY, algorithms=[JWT_ALGORITHM])`. Both creation and decoding via pyjwt are genuinely implemented. **Verified.**
- `Authorization: Bearer <token>` parsing is implemented in `get_jwt_token()` (lines 20-24), consistent with AD-17's claim about the header format. **Verified.**

### The shared-secret claim — FALSE as stated

AD-17's rule text: *"decoding/verifying with the same secret and algorithm as units-backend, shared via the same root `.env`."*

Traced `JWT_SECRET_KEY` / `JWT_ALGORITHM`:

- `utilities/config.py` lines 19-20:
  ```python
  JWT_SECRET_KEY = settings.SECRET_KEY
  JWT_ALGORITHM = "HS256"
  ```
- `property_management/settings.py` line 19:
  ```python
  SECRET_KEY = 'django-insecure--w_9ca8o1wlh-l3foy8=g*x%9ay90j@2#3&pntlrv$wausuo8&'
  ```
  This is a **hardcoded Python literal**, not `os.environ.get('SECRET_KEY', ...)` or any `.env`-backed value. It is the default `django-insecure-...` string Django's `startproject` scaffolding generates, left in place in source.
- Checked the root `.env` (`/home/rahul/Optiex/Docufy/Code/units-architecture/.env`) directly: it contains **no `SECRET_KEY` or `JWT_SECRET_KEY` entry at all**. It has unrelated `AWS_SECRET_KEY` / `SES_AWS_SECRET_KEY` values (S3/SES credentials), which are not the Django/JWT secret and are not what AD-17 is describing.

**Consequence:** AD-17's stated sharing mechanism — "same secret ... shared via the same root `.env`" — does not exist. The actual secret is a hardcoded string baked into `property_management/settings.py`, private to the units-backend codebase/container. For Finance (a separate Django project/codebase under `microservices/units-finance`) to validate the same JWTs, it would need to either:
  (a) hardcode a copy of that same literal string in its own settings.py (which is not what AD-17 says, is fragile, and silently breaks the moment either project's `SECRET_KEY` is rotated or genuinely env-ified), or
  (b) the spine's Deferred/AD-2/AD-3 `.env` normalization work would need to additionally *move* `SECRET_KEY` out of `settings.py` into the root `.env` as `JWT_SECRET_KEY` (or similar) and update `utilities/config.py` to read it from there — which is a real, but currently unstated and undone, prerequisite.

AD-17 is written as if the shared-`.env` mechanism already exists and merely needs to be reused (consistent with how AD-2/AD-3 correctly describe DB_* vars, which *are* genuinely in `.env`). That parallel does not hold for the JWT secret. As written, AD-17's rule is unsatisfiable without additional, unstated engineering work.

---

## Check for unverified technology/version claims

Re-read the full Invariants & Rules section, the Inherited Invariants table, and the parts of the Stack table implicated by this update (AD-17/AD-18 don't introduce new Stack rows, but AD-17 does assert `pyjwt` is used, which is a technology claim worth checking independent of the three named files):

- `pyjwt` usage — verified above (installed, pinned in requirements.txt, imported as `jwt` in `jwt_token.py`).
- `django==4.0.0` (referenced by AD-13, unchanged by this update, but re-checked as part of due diligence since AD-13 sits adjacent to the reconciled section) — confirmed still pinned exactly as `django==4.0.0` in `docker_config/python_config/requirements.txt` line 2, matching the existing claim. No drift.
- No new library, framework, or version number is introduced anywhere in the Inherited Invariants table, amended AD-1, AD-17, or AD-18 beyond what was already stated pre-update (`prepare_response()`, `pyjwt`, `Authorization: Bearer`, `get_pmc_ids_for_user()`) — all pre-existing claims, all now verified against real code.
- The update does not add any new Stack-table row, new pinned version, or new "current stable/LTS" claim. **No unverified technology/version claims found.**

---

## Summary of findings

| Item | Status |
| --- | --- |
| `prepare_response()` exists, returns `{content, message, status}` | Verified |
| `get_pmc_ids_for_user()` exists as described | Verified |
| `jwt_token.py` implements JWT creation/decoding via pyjwt | Verified |
| `Authorization: Bearer <token>` handling | Verified |
| AD-17 claim: same secret/algorithm as units-backend, shared via same root `.env` | **False as stated** — `SECRET_KEY` is hardcoded in `property_management/settings.py`, not present in root `.env` at all |
| New/unverified technology or version claims introduced by this update | None found |

## Recommendation

Amend AD-17 (or add a companion AD) to state the actual prerequisite explicitly: either (a) units-backend's `SECRET_KEY` must first be extracted into the root `.env` (e.g. as `JWT_SECRET_KEY` or reusing `SECRET_KEY`) and `utilities/config.py` updated to read it from there before Finance can validate against it, or (b) Finance duplicates the literal hardcoded string as a stopgap (explicitly flagged as technical debt, since it silently desyncs on rotation). As currently worded, AD-17 describes a mechanism that does not exist in the codebase today.
