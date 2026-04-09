# Design: Security & Stability Fixes — posembarque

**Date:** 2026-04-09
**Status:** Approved (fixes derived from code review, user requested all be applied)

---

## Context

A full code review of `app.py` identified 11 issues ranging from automatic logout causes to security vulnerabilities. All fixes apply to the existing codebase with minimal structural change.

---

## Fixes

### Fix 1 — SECRET_KEY validation on startup (Critical)
**File:** `app.py` lines 45–50
**Problem:** If `.env` fails to load, `SECRET_KEY` defaults to `''`. Flask uses it silently; all existing session cookies become invalid, logging out every user.
**Fix:** Replace `logger.error` warning with `raise RuntimeError` so the app refuses to start with an empty or short key (< 32 chars).

### Fix 2 — Remove `current_user` access from `before_request` (High)
**File:** `app.py` lines 89–95
**Problem:** Accessing `current_user.is_authenticated` triggers `load_user()` on every single request (including AJAX polls every 3s). If `load_user` fails (DB error, no cache), the user becomes anonymous for that request and is redirected to login.
**Fix:** Remove the log line accessing `current_user`. Keep only `session.modified = True`.

### Fix 3 — Close ping cursor in `get_db_connection` (Medium)
**File:** `app.py` lines 171 and 185
**Problem:** `conn.cursor().execute("SELECT 1")` creates a cursor that is never closed, leaking resources over time.
**Fix:** Assign cursor to variable and call `.close()` after execute.

### Fix 4 — Thread lock for `_recriar_pool` (Medium)
**File:** `app.py` line 152
**Problem:** `_recriar_pool()` is not thread-safe. If two simultaneous requests both detect a bad pool and call this, both close and recreate it, potentially orphaning connections.
**Fix:** Add `threading.Lock` and acquire it during pool recreation.

### Fix 5 — CSRF protection on all forms (High)
**Files:** `app.py`, all templates with POST forms
**Problem:** No CSRF tokens on any form. Any site can submit forms as an authenticated user.
**Fix:**
- `app.py`: `CSRFProtect(app)`, add `WTF_CSRF_ENABLED = True`
- Templates: add `<input type="hidden" name="csrf_token" value="{{ csrf_token() }}">` to each POST form
- `dashboard.html`: add `<meta name="csrf-token">` and update all `fetch()` POST calls to include `X-CSRFToken` header

### Fix 6 — Hash reset tokens before storing (Medium)
**File:** `app.py` lines 986–992 and 1021–1039
**Problem:** Password reset tokens are stored as plain text in the DB. A DB dump exposes active tokens.
**Fix:** Store `hashlib.sha256(token.encode()).hexdigest()` in DB; look up by hash; send raw token in email.

### Fix 7 — Add gunicorn timeout to systemd unit (Medium)
**File:** `/etc/systemd/system/posembarque.service`
**Problem:** No `--timeout` set; default is 30s. Slow routes (PDF export, reports) can trigger worker kills.
**Fix:** Add `--timeout 120` to `ExecStart`.

### Fix 8 — Close cursor in `load_user` exception path (Low)
**File:** `app.py` lines 120–139
**Problem:** If `cur.execute()` raises, `cur` is never closed before connection is returned to pool.
**Fix:** Track `cur = None`, close in `finally`.

### Fix 9 — `requirements.txt` — add flask-wtf (housekeeping)
**File:** `requirements.txt`
**Problem:** `flask-wtf` is installed but not declared in requirements.
**Fix:** Add `flask-wtf` line.

---

## Explicitly excluded

- **Cookie size** — session data is small and flash messages are consumed on render; no code change needed.
- **`registrar_log` in except blocks** — already wrapped in try/except; failure logs to file; no change needed.
- **Cursor close in `esqueci_senha`** — connection release handles this implicitly; minor.

---

## Implementation order

1. `app.py` — all Python fixes (1, 2, 3, 4, 6, 8)
2. `app.py` + templates — CSRF (5)
3. `requirements.txt` — housekeeping (9)
4. `posembarque.service` — gunicorn timeout (7)
5. Commit, push, reload systemd + restart gunicorn
