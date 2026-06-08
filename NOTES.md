# World Cup 2026 Predictions — NOTES

Progress log, key decisions, and what's needed to resume work.

## Status (2026-06-08)

Scaffolded and smoke-tested a working Flask + SQLAlchemy + Postgres app. Auth, admin
round/fixture management, prediction submission + locking, scoring, and both
leaderboards are implemented and manually verified end-to-end (register → admin
creates round → assigns fixture → user predicts → fixture scored → leaderboards update).

Also now wired up: the background polling scheduler (`app/scheduler.py`, see below) —
live polling during match windows, daily sync otherwise, all logged to `PollLog` and
visible in the admin panel (`/admin/polling`, plus a "last run" summary on `/admin/`).

**Not yet built** (natural next steps):
- Styling/UX pass — current templates are functional Bootstrap, not polished.
- Tests (none yet — verified manually via curl + a Python REPL script during scaffolding).
- Railway deployment itself (Procfile is written but untested against a real Railway project).

## Background polling — app/scheduler.py

Implemented with APScheduler's `BackgroundScheduler`, started inside the Flask process
by `create_app` (see `maybe_start_scheduler`). Two jobs:

- **`live_poll`** — fixed 3-minute interval, every tick. It computes "today's live
  window" (`get_live_window`): `(earliest kickoff today − 15 min)` to
  `(latest kickoff today + 105 min assumed match length + 30 min)`. If "now" isn't in
  that window, the tick is a no-op (and isn't logged — only real polls/syncs hit
  `PollLog`, so the log stays meaningful rather than filling with 480 "skipped" rows/day).
  If it is, it calls `sync_fixtures_and_results(date_from=today, date_to=today)` —
  scoped to just today's matches, since that's all that can change mid-window — updates
  scores, rescores affected predictions, and records a `PollLog` row.
- **`daily_sync`** — cron trigger at 06:00 UTC (configurable via `DAILY_SYNC_HOUR_UTC`/
  `DAILY_SYNC_MINUTE_UTC`), runs the full unscoped sync to catch fixture changes and
  newly confirmed knockout matchups, also logged to `PollLog`.

Every run — live, daily, or the admin's manual "Sync now" button — writes a `PollLog`
row (`mode`, success/failure, created/updated/rescored counts, and notes e.g. ET/penalty
fixtures flagged for the admin to verify). The admin dashboard shows the most recent
run; `/admin/polling` shows the last 100.

**Single-process assumption**: the scheduler runs in-process, so multiple gunicorn
workers would each start their own and poll redundantly (and write duplicate `PollLog`
rows). The `Procfile` pins `gunicorn run:app --workers 1` for this reason — if the app
ever needs to scale web workers, move the scheduler to a dedicated worker process or an
external scheduler instead. `ENABLE_SCHEDULER=false` disables it entirely (e.g. for
local scripts/tests — used throughout this scaffolding's test runs to avoid hitting the
live API on a dummy key). The Flask reloader (`flask run` in debug mode) is also guarded
against double-starting via the `WERKZEUG_RUN_MAIN` check in `maybe_start_scheduler`.

## Key decisions & nuances

### Knockout scoring ("no draws") — app/scoring.py
The spec says predictions are judged on the 90-minute score, but also that "in knockout
rounds there are no draws, so correct result means picking the winning team regardless
of score." These two statements only fit together if:
- **Exact score (16 pts)**: predicted score == 90-minute score, always — this is what
  "includes the 6 for the result" means, so an exact match never needs the result check.
- **Correct result (6 pts), group stage**: predicted W/D/L outcome == 90-minute outcome.
- **Correct result (6 pts), knockout**: predicted *winner* == the team that actually
  advanced (`Fixture.winner`), regardless of the 90-minute scoreline or extra time/
  penalties. A user who predicts a draw for a knockout match cannot earn result points
  (there's no "correct" draw outcome to match) unless their exact score also happens to
  match the 90-minute scoreline.

This requires storing **two** pieces of truth per fixture: `home_score_90`/
`away_score_90` (for exact-score comparison and group-stage result comparison) and
`winner` (HOME/AWAY/DRAW — DRAW only possible in group stage, for knockout result
comparison).

### football-data.org data gap — app/sync.py
The API's `score.fullTime` is the score *as the match ended* — for knockout matches that
go to extra time, this includes ET goals, not the 90-minute score we need. The free tier
doesn't appear to expose a clean "90-minute" breakdown. **Mitigation**: `sync_fixtures_and_results`
stores `fullTime` as the 90-minute score by default (correct for the ~90% of matches
decided in regulation), flags any fixture where `score.duration != "REGULAR"`, and the
admin fixtures page (`/admin/fixtures`) lets the admin manually correct
`home_score_90`/`away_score_90`/`winner`/`is_knockout` and re-trigger scoring for that
fixture. **Action needed during the tournament**: after every knockout match that goes
to ET/penalties, an admin must check the flagged fixture and fix the 90-minute score by
hand (e.g. from the match report) before re-scoring.

### Round lifecycle: DRAFT → ACTIVE → COMPLETE
Originally rounds were strictly sequential ("create the next one only once the current
is complete"), but that blocked the admin from prepping the next round while the
current one was still live — especially painful for knockout rounds where matchups need
curating as soon as they're confirmed, often mid-round. Replaced with an explicit
`Round.status` state machine (`ROUND_STATUS_DRAFT/ACTIVE/COMPLETE` in `app/models.py`):

- **DRAFT** — admin is naming it and assigning fixtures; completely invisible to regular
  users (`main.round_results` 404s for non-admins, `get_active_round`/`get_draft_round`
  in `app/round_helpers.py` keep the two query paths separate). The admin can create and
  populate a draft at any time, including while another round is active — there's no
  "must be complete first" gate on creation anymore, only "no other draft already exists"
  (`admin.create_round`).
- **ACTIVE** — the single round visible to users for predictions/results. Promoted from
  DRAFT via `admin.publish_round`, which enforces: must currently be DRAFT, no other round
  is already ACTIVE, at least one fixture is assigned (so users never see an empty round),
  and the lock time hasn't already passed (so publishing doesn't instantly lock it).
- **COMPLETE** — archived for history/leaderboards. Promoted from ACTIVE via
  `admin.complete_round`, which requires the round to actually be locked first (can't
  archive a round predictions are still open on); it warns but doesn't block if
  `Round.all_fixtures_settled` is false, since results can still trickle in/be corrected
  and rescored afterwards.

`Round.all_fixtures_settled` (renamed from `is_complete`) is a *readiness* check — locked,
has fixtures, every fixture finished/scored — distinct from `status == COMPLETE`, which is
an explicit admin decision. The admin dashboard surfaces both the current `active_round`
and any `draft_round` being prepared side by side. `get_round_for_leaderboard()` falls
back to the most recently archived round between cycles (active just archived, next not
yet published) so the round leaderboard doesn't go blank. `sequence` is still
auto-assigned as `previous + 1`; old rounds are never deleted. Fixture
assignment/removal is still blocked once a round locks (`assign_fixtures`/
`unassign_fixture` check `round.is_locked`), so the admin can keep curating a draft or
active round right up to its lock deadline.

### Round lock time
Computed dynamically as `min(kickoff_at across the round's fixtures) - 5 minutes`
(`Round.lock_time` in app/models.py) rather than stored — it can only be known once
fixtures are assigned, and naturally updates if the admin reassigns fixtures before lock.

### "Current round" for leaderboards/results
`app/round_helpers.py` (status-based, see [[Round lifecycle]] above):
- `get_active_round()` — the single round visible to users for predictions/results.
- `get_draft_round()` — the round the admin is preparing (admin-only).
- `get_round_for_leaderboard()` — `get_active_round()`, falling back to the most recently
  archived round between cycles so the leaderboard doesn't go blank while the admin
  preps the next one.

### Live score display — main.round_results / round_live_scores
The "predictions vs results" page (`main/round_results.html`) shows each fixture's
current state: a `LIVE <minute>'` badge with the running score while
`Fixture.status` is `IN_PLAY`/`PAUSED` (`Fixture.is_live`), the final score (with
knockout winner) once finished, or "Not started" beforehand. Since the free
football-data.org tier doesn't reliably expose a live "minute" field, `Fixture.elapsed_minutes`
approximates it from wall-clock time since kickoff (capped at 90). The page polls a
small JSON sibling endpoint (`main.round_live_scores`, same draft/lock visibility rules
as `round_results`) every 3 minutes via `fetch` to refresh scores in place without a
full reload — matching the scheduler's live-poll cadence (app/scheduler.py), with a
visible note that scores can run up to ~10 minutes behind real time as a result.

### Live round leaderboard — main.round_leaderboard_view / round_leaderboard_live
`app/leaderboards.round_leaderboard` returns `(user, round_points, tournament_points)`
rather than just round points — showing both side by side lets a user see, as results
land mid-round, both how this round is going *and* where it leaves them overall, without
a separate lookup. The page (`main/round_leaderboard.html`) auto-refreshes via the same
`fetch`-every-3-minutes pattern as the live score feed (`main.round_leaderboard_live`,
[[Live score display]]), rebuilding the table in place from JSON so points update as
fixtures finish and `score_fixture` reruns — no manual reload needed. If the active round
itself changes between polls (archived/published mid-session), the refresh is a no-op and
a future full page load picks up the new round, rather than splicing mismatched data in.

### Password hashing
Explicitly set to `pbkdf2:sha256` in `User.set_password` — werkzeug's default (`scrypt`)
needs `hashlib.scrypt`, which isn't available on every Python build (hit this locally
with a LibreSSL-linked Python 3.9). pbkdf2 is broadly compatible and still solid.

### Admin auth
Not a separate `Admin` model — `User.is_admin` boolean. The single superuser is
provisioned/promoted via `flask seed-admin`, which reads `ADMIN_USERNAME`/`ADMIN_EMAIL`/
`ADMIN_PASSWORD` env vars (wired into the Railway `release` step in the Procfile).
Additional admins, if ever needed, would have to be promoted by hand in the DB —
there's no UI for it (open registration + a single curated superuser per the spec).

## Project layout

```
config.py              — env-driven config (DB URL normalization, API keys, scoring constants)
run.py                 — entry point / gunicorn target (loads .env via python-dotenv)
app/
  __init__.py          — app factory, blueprint registration, `flask seed-admin` CLI command
  extensions.py        — db, migrate, login_manager, csrf (singletons)
  models.py            — User, Round, Fixture, Prediction
  forms.py             — WTForms: registration/login, dynamic per-round prediction form, CSRFForm
  scoring.py           — calculate_points / score_fixture (the rules described above)
  leaderboards.py      — round_leaderboard / tournament_standings queries
  round_helpers.py     — get_open_round / get_current_or_most_recent_round
  football_data.py     — football-data.org v4 API client (matches endpoint)
  sync.py              — upserts fixtures/results from the API, flags ET/penalty matches, triggers scoring
  admin_utils.py       — admin_required decorator
  blueprints/
    auth.py            — register / login / logout
    main.py            — landing, dashboard, leaderboards, round results (predictions vs actual)
    predictions.py     — view/submit predictions for the currently open round
    admin.py           — rounds, fixture assignment, manual fixture correction, sync trigger, user list
  templates/           — Bootstrap 5 (CDN) templates per blueprint
```

## Local dev

```
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env   # fill in SECRET_KEY, DATABASE_URL, FOOTBALL_DATA_API_KEY, ADMIN_*
flask db upgrade       # creates tables (migrations/ already initialized)
flask seed-admin       # creates/promotes the superuser from ADMIN_* env vars
flask run
```

## Railway deployment

`Procfile` defines:
- `web: gunicorn run:app`
- `release: flask db upgrade && flask seed-admin` — runs migrations and (re)provisions
  the superuser on every deploy.

Set `DATABASE_URL` (Railway Postgres plugin provides this — `config.py` normalizes
`postgres://` → `postgresql://` automatically), `SECRET_KEY`, `FOOTBALL_DATA_API_KEY`,
and `ADMIN_USERNAME`/`ADMIN_EMAIL`/`ADMIN_PASSWORD` as Railway environment variables.

## Round structure (admin will create these manually after deploy)

1. Group Stage Week 1 (~11–15 June)
2. Group Stage Week 2 (~16–20 June)
3. Group Stage Week 3 (~21–27 June)
4. Round of 32
5. Round of 16
6. Quarter-finals
7. Semi-finals & Third Place
8. Final

Sequence numbers above are suggestions for the `sequence` field — fixtures sync in
unassigned and the admin curates which belong to each round (essential for the knockout
stages, where matchups aren't known until earlier rounds finish).
