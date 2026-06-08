# World Cup 2026 Predictions — NOTES

Progress log, key decisions, and what's needed to resume work.

## Status (2026-06-08)

Scaffolded and smoke-tested a working Flask + SQLAlchemy + Postgres app. Auth, admin
round/fixture management, prediction submission + locking, scoring, and both
leaderboards are implemented and manually verified end-to-end (register → admin
creates round → assigns fixture → user predicts → fixture scored → leaderboards update).

Also wired up: the background polling scheduler (`app/scheduler.py`, see below) — live
polling during match windows, daily sync otherwise, all logged to `PollLog` and visible
in the admin panel (`/admin/polling`, plus a "last run" summary on `/admin/`).

**Built this session:**
- **Deleting a user now cascades and disappears them from the players grid
  immediately.** `admin.delete_user` previously *blocked* deletion outright if the
  user had any predictions/round entries; it now lets the delete go through and
  `cascade="all, delete-orphan"` on `User.predictions`/`User.round_entries`
  (`app/models.py`) takes their predictions, round entries and pot opt-ins with
  them in one transaction - closing the gap where a deleted user could keep
  showing up in `/players` (built from `RoundEntry.opted_in` rows). See
  [[Admin auth]].
- **Display names, separate from (private) login usernames.** `User.display_name`
  (required, set at registration or admin creation, editable any time via the new
  `/auth/profile` page) is now what's shown everywhere another player can see you —
  players grid, leaderboard, pot/finance rows, avatars, navbar — while `username`
  reverts to being purely a login credential. See [[Display names]].
- **Per-round configurable stake.** `Round.stake_amount` (admin-set when creating a
  round, defaulting to £5.00 if left blank) replaces the old hardcoded `STAKE_AMOUNT`
  everywhere pot/settlement figures are calculated (`app/finance.py`,
  `main.round_opt_in`, `predictions.my_predictions`). See [[Per-round stake]].
- **Home screen polish**: "Fixture" → "Fixtures" column header; dropped the standalone
  "Players" `<h1>` and the "N players in · £5.00 stake each" line (the pot amount
  alone says enough), and added a "X/N Players" line directly above the grid showing
  how many of the app's registered users have opted in to the live round.
- **Admin section is now fully mobile/PWA-compatible — no more breaking out to Safari.**
  Every admin page (dashboard, rounds, round detail, fixtures, users, polling,
  finance) was rebuilt around a shared `admin/_mobile.html` card-list layout
  (`.admin-page`/`.admin-list`/`.admin-row`) replacing wide multi-column
  `<table>`s, eliminating horizontal scroll on iPhone. The actual cause of the
  standalone-mode "pop out to Safari" bug turned out to be an open-redirect
  pattern (`redirect(request.referrer or url_for(...))`) in three `admin.py`
  routes — removed in favour of plain `url_for(...)` redirects, which is both
  the fix and a security hardening (closes a client-controlled redirect vector).
  See [[Admin mobile layout]].
- **Username-only sign-up/login — email is now admin-seed-only.** Registration and
  the admin's "add user" form collect just username + password; `User.email` is now
  nullable/non-unique and `NULL` for everyone but the seeded admin, and login
  switched from email to username accordingly. See [[Admin auth]].
- **Redesigned the PWA app icons** as a classic black-and-white hex-panel football
  (axial hex grid, dark "pentagon" cells picked by the `(q - r) % 3 == 0` residue
  rule that keeps them mutually non-adjacent — the defining trait of a real
  truncated icosahedron), generated as SVG polygons and rasterized to PNG by the
  same hand-rolled renderer as before. See [[PWA support]].
- **Navigation redesign, round 2 — Home is the hub, no persistent nav.** Split the
  players home screen into Home / My predictions / Leaderboard (a follow-up to the
  previous session's split, which had added a fixed bottom tile bar — removed again
  in favour of "Home is always the landing screen, reached and returned to via tiles
  and a back link only"). Also: moved the admin entry point to a small corner gear
  icon on Home (admin-only), trimmed copy further (dropped the "good luck"/
  "predictions hidden here" hints, the score key/legend, and the "you've entered
  X of Y" counts on both Home and My predictions as redundant - the grid and the
  entry form make actual progress obvious without a number), shortened the footer,
  and added flag emoji next to every team name app-wide. Also redesigned the
  players grid's per-user header: username above a circular avatar with a small
  round-points "moon" tucked into its bottom-right edge (`.avatar-orbit`/
  `.points-moon` in `main/players.html`), replacing the separate "N pts" pill
  stacked below - more compact, reads as one unit per player. See
  [[Navigation redesign]] and [[Team flags]].
- **Full PWA support** — installable on iOS via Safari "Add to Home Screen", with
  an app shell cached for offline use and an "update available" banner that
  appears automatically on deploy. See [[PWA support]].
- **Admin user management** — `/admin/users` can now create and delete accounts
  directly (with an "Admin" checkbox), rather than requiring hand-edits to the DB
  to promote additional admins. See [[Admin auth]].
- **Live match scores on the results page** — `main/round_results.html` shows a live
  `LIVE <minute>'` badge + running score, final score + winner, or "Not started", and
  auto-refreshes every 3 minutes via `fetch` against the new `main.round_live_scores`
  JSON endpoint. See [[Live score display]].
- **Real-time round leaderboard** — `main/round_leaderboard.html` now shows round points
  *and* tournament total side by side and auto-refreshes the same way, against the new
  `main.round_leaderboard_live` JSON endpoint and a rewritten `leaderboards.round_leaderboard`
  that returns both totals per user. See [[Live round leaderboard]].
- **Switched DB driver from psycopg2 to psycopg v3** (`psycopg[binary]`) to fix a Railway
  boot crash (`ImportError: libpq.so.5`) — see [[psycopg v3 driver]].
- **Startup-time migrations + admin seeding fallback** (`app/__init__.py:run_startup_tasks`)
  to fix two Railway-only bugs (login 500s, a DB error near dashboard loads) both rooted
  in the same cause: Railway never runs the Procfile's `release:` line. See
  [[Railway deployment]] for the full story and current status.

## Current Railway deployment status

**As of this session's fixes, NOT yet redeployed/reverified on Railway** — the three
fixes above (psycopg v3 switch, startup migrations, startup admin seeding) were made in
response to real errors reported from a live Railway deployment, verified locally
(SQLite, cold + warm boot, idempotency), but **not yet confirmed against Railway's actual
Postgres**. Next session should deploy this branch and confirm:
- App boots without the `libpq.so.5` crash (psycopg v3).
- Startup logs show migrations applying cleanly against Railway's Postgres (watch for
  any Postgres-specific migration quirks that don't show up against SQLite).
- Login works (admin user seeded automatically, no more 500).
- Dashboard loads without the `relation "fixtures" does not exist` error.
- The live-poll scheduler runs cleanly once the schema exists (it starts immediately on
  boot, in-process — see [[Background polling]]).

**Known risk to watch for**: `run_startup_tasks` runs `flask_migrate.upgrade()` on
*every* boot, in-process, with `--workers 1` (single gunicorn worker, per
[[Background polling]]'s single-process assumption) — so this should be safe and
sequential. If web worker count is ever increased, multiple workers racing to run
migrations on boot would be a real problem; that'd need to move to a proper Railway
pre-deploy/release-command config (Railway supports this via `railway.json`/`railway.toml`,
neither of which exists in this repo yet) rather than running at app-boot time.

## Known issues / open questions

- **Railway deploy unverified** (see above) — this is the most important thing to close
  out next session.
- **No `railway.json`/`railway.toml`** — Railway doesn't run the Procfile's `release:`
  line, and the repo has no Railway-native config for a release/pre-deploy step either.
  The startup-time fallback works around this, but a proper `railway.json` with a
  `deploy.releaseCommand` would be the more idiomatic fix if/when web workers scale
  beyond 1.
- **psycopg v3 switch is locally-verified only** — confirmed the engine resolves to the
  `psycopg` dialect and the app boots fine against SQLite; not yet run against a real
  Postgres instance (no local Postgres/Docker available in this environment).
- **Live "minute" is approximated** (`Fixture.elapsed_minutes`, wall-clock since kickoff,
  capped at 90) because the free football-data.org tier doesn't reliably expose a live
  minute field — acceptable per spec but worth knowing if scores look "ahead" of the
  badge during stoppage time.

**Not yet built** (natural next steps, unchanged from before this session):
- Styling/UX pass — current templates are functional Bootstrap, not polished.
- Tests (none yet — verified manually via curl + a Python REPL script during scaffolding,
  and via local SQLite smoke tests for this session's fixes).

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

### Round leaderboard — leaderboards.round_leaderboard
`app/leaderboards.round_leaderboard` returns `(user, round_points, tournament_points)`
rather than just round points — showing both side by side lets a user see, as results
land mid-round, both how this round is going *and* where it leaves them overall, without
a separate lookup. **Superseded by [[Navigation redesign]]**: the live-polling
`main.round_leaderboard_live` JSON endpoint and its auto-refreshing page
(`main/round_leaderboard.html`) were removed in favour of a plain server-rendered
`main.leaderboard` screen — the financial settlement it shows is computed per-load
anyway, so a 3-minute live poll added little for the added complexity.

### Navigation redesign — Home is the hub, dedicated screens for the rest
The previous "single home screen" layout (players grid + inline prediction entry +
both leaderboards all on `main.players`) got cramped as more was bolted onto it
(opt-in/pot status, financial summaries, live polling). Split back out into a small
set of focused screens reached *only* from Home — there's no persistent nav (an
earlier pass added a fixed bottom tile bar, but that was removed in favour of
"home is always the landing screen, everything else is one tap away and one tap
back"):

- **`main.players` ("Home")** — the landing screen and hub: pot/opt-in status and
  the read-only players grid (clock icons hide everyone's picks, including your
  own, until the round locks — editing your own moved off this screen entirely),
  plus two small icon-only quick-link tiles (`players-actions` / `players-action-tile`
  — a pencil for My predictions, a trophy for Leaderboard, right-aligned and kept
  deliberately unobtrusive so the grid stays the focus) to the other two screens. Admins
  get a small gear icon (`.admin-cog`) fixed to the top-right corner, linking to
  `admin.dashboard` — the only admin entry point on this screen, and invisible to
  regular users.
- **`predictions.my_predictions` ("My predictions")** — dedicated screen
  (`predictions/edit.html`) for entering/editing your own picks before the
  deadline. Requires having opted in first (Home prompts for that; landing here
  without opting in redirects back with a flash nudge). On successful save it
  redirects straight to `main.players` — no back button, since saving *is* the
  exit action.
- **`main.leaderboard` ("Leaderboard")** — dedicated screen (`main/leaderboard.html`,
  replacing the old in-page tabs on `main.players`) with the same two tabs (this
  round's pot standings + season table). Has a `&larr; Home` back link
  (`.leaderboard-back`) since, unlike Predictions, landing here doesn't have a
  natural exit action of its own.

`main.round_leaderboard_view`/`main.tournament_leaderboard_view` still redirect
(now to `main.leaderboard`) to keep old bookmarks alive. The live-refreshing
`main.round_leaderboard_live` JSON endpoint was removed along with the in-page
leaderboard it fed — `main.leaderboard` is now a plain server-rendered page (the
financial settlement it shows is computed per-load anyway, so a live poll added
little). `_cell` in `main.py` lost its `editable` status — every cell is now either
`hidden` or one of the post-lock states, since editing happens on its own screen.
The score key/legend under the grid was also removed (the icons/highlights are
considered self-explanatory enough on their own).

### Team flags — app/teams.py
`flag_for`/the `flag` template filter render a national flag emoji next to team
names wherever they appear (players grid, My predictions, admin fixtures/round
screens) — format `{{ team | flag }} {{ team }}`. Built on a name→ISO-3166-1-alpha-2
lookup (`_TEAM_CODES` in `app/teams.py`, converted to emoji via regional indicator
symbols) covering the 2026 hosts and the confederations' likely 48 qualifiers, plus
common aliases football-data.org might return (e.g. "USA"/"United States",
"Korea Republic"/"South Korea"). England/Scotland/Wales use literal emoji tag
sequences (no ISO code exists for home nations). Unrecognised names render no flag
(`flag_for` returns `""`) rather than guessing — **worth spot-checking once real
fixtures sync in**, since the UEFA play-off path wasn't finalised as of this
session's knowledge cutoff and football-data.org's exact naming (short vs. long
form, diacritics) is unconfirmed against the live feed.

### PWA support — app/static/{manifest.json,sw.js,icons/}, app/blueprints/main.py
Installable as a standalone app (no browser chrome) via iOS Safari's "Add to
Home Screen", and works offline for the cached shell:
- **`app/static/manifest.json`** — name "World Cup Predictions" / short name
  "WC Predictions", `display: "standalone"`, `start_url: "/"`, dark
  `background_color`/`theme_color` (`#121417`, matching the grid/players dark
  theme), and 192×192 / 512×512 icons (`maskable` so iOS/Android can crop to
  their own shape). The icons (`app/static/icons/icon-{192,512}.png`, plus
  `icon.svg` kept alongside as the editable source) are a classic black-and-white
  hex-panel football, generated by `scripts/gen_icons.py`. No SVG/raster toolchain
  exists in this env (no Pillow, cairosvg, rsvg-convert, Inkscape, ImageMagick,
  headless Chrome...), so the script builds the ball as actual SVG polygon geometry
  and rasterizes that *same* shape list to PNG with a tiny hand-rolled renderer
  (point-in-polygon fill + a minimal PNG encoder over zlib/struct) — SVG and PNG
  are two views of one set of polygons, not drawn independently. The dark/light
  panel layout comes from a pointy-top hex grid where axial cells satisfying
  `(q - r) % 3 == 0` are filled dark: that residue rule is what gives a real
  truncated icosahedron its defining property (no two dark panels ever touch,
  each fully ringed by light ones), so it reproduces the classic ball look without
  hand-placing 32 panels. Re-run the script if the glyph ever needs to change —
  there's no separate source image to hand-edit beyond the generated `icon.svg`.
- **`base.html`** links the manifest, sets `theme-color`, and adds the
  `apple-mobile-web-app-*` meta tags + `apple-touch-icon` Safari needs to treat
  the home-screen launch as a standalone app rather than a bookmark.
- **Service worker** (`app/static/sw.js`, served at `/sw.js` — not
  `/static/sw.js` — via `main.service_worker` so its scope covers the whole
  app, not just `/static/`) caches the shell (`/`, manifest, icons) on install,
  serves pages network-first-falling-back-to-cache (so logged-in players still
  see live scores when online, but get *something* offline), and static assets
  cache-first. Bumping `CACHE_NAME` in `sw.js` is what makes a deploy "visible"
  to the browser — any byte change to the file triggers the update flow below.
- **"Update available" banner** (`#pwa-update-banner` + inline script in
  `base.html`) — the standard waiting-worker pattern: a new service worker
  installs alongside the active one and sits in `waiting` rather than taking
  over immediately (so mid-session users aren't yanked onto a new version);
  the page detects this via `registration.installing`'s `statechange` →
  `installed` (with an existing `controller`, i.e. this is an update, not a
  first install) and shows the banner. Clicking **Refresh** posts
  `{type: "SKIP_WAITING"}` to the waiting worker, which calls `self.skipWaiting()`;
  the resulting `controllerchange` event reloads the page once, now served by
  the new version. Dismissing just hides the banner for this session — it'll
  reappear on the next load until the user refreshes.

### Admin mobile layout — app/templates/admin/{_mobile.html,*.html}, app/blueprints/admin.py
Audited every admin page for iPhone/PWA compatibility — no horizontal scrolling,
compact and readable on a small screen, and (critically) staying inside the
installed standalone shell rather than popping out to Safari:
- **`admin/_mobile.html`** is a small CSS partial `{% include %}`'d at the top of
  every admin template. It defines a shared card-list vocabulary —
  `.admin-page` (capped width), `.admin-list`/`.admin-row` (a flex column of
  bordered cards replacing `<table>` rows), `.admin-row-head`/`-meta`/`-body`/
  `-actions` (sub-regions within a card), and `.admin-empty` — plus a blanket
  `.admin-page, .admin-page * { min-width: 0; overflow-wrap: anywhere; }` guard
  so no cell content (long usernames, team names, log details...) can ever force
  the page wider than the viewport.
- **Every admin page rebuilt on this vocabulary**, replacing wide multi-column
  `<table>`s with one `.admin-row` per record: `users.html`, `rounds.html`,
  `round_detail.html` (both the assigned-fixtures list and the unassigned
  checkbox-select list, the latter using `<label>`-wrapped checkboxes for
  easy tapping), `fixtures.html` (header line + meta line + inline edit form
  per fixture), and `polling.html`. `dashboard.html` keeps its existing card
  layout but switches the stat row to `col-6 col-md-3` (two-up on phones) and
  the action buttons to `flex-wrap` + `btn-sm`. `finance.html` keeps its
  per-round dark cards but swaps the embedded `table-dark table-striped` for a
  `list-group list-group-flush` of compact rows.
- **Found and fixed the actual cause of admin pages "breaking out of the PWA"**:
  three routes in `admin.py` (`unassign_fixture` ×2, `edit_fixture`) redirected
  via `redirect(request.referrer or url_for(...))`. `request.referrer` is a
  client-supplied absolute URL — besides being an open-redirect vector in its
  own right, a referrer whose origin/scheme doesn't exactly match the installed
  PWA's registered scope is what makes iOS standalone mode kick the navigation
  out to Safari. Replaced all three with plain `redirect(url_for(...))`, which
  is always same-origin/relative and both closes the security hole and keeps
  the whole admin section inside the shell. Confirmed via grep that no
  `request.referrer` usage remains anywhere in `app/`.
- **Made the service worker's root scope explicit** — `navigator.serviceWorker
  .register("/sw.js", { scope: "/" })` in `base.html` — so it's unambiguous that
  `/admin/*` (and every other route) is handled by the worker, not just whatever
  page the user happens to land on first. (It was already covered implicitly,
  since `/sw.js` defaults to scope `/`, but explicit beats implicit here.)

### Password hashing
Explicitly set to `pbkdf2:sha256` in `User.set_password` — werkzeug's default (`scrypt`)
needs `hashlib.scrypt`, which isn't available on every Python build (hit this locally
with a LibreSSL-linked Python 3.9). pbkdf2 is broadly compatible and still solid.

### Admin auth
Not a separate `Admin` model — `User.is_admin` boolean. The single superuser is
provisioned/promoted via `flask seed-admin`, which reads `ADMIN_USERNAME`/`ADMIN_EMAIL`/
`ADMIN_PASSWORD` env vars (wired into the Railway `release` step in the Procfile).

**Email is now admin-seed-only.** Sign-up/login and `AdminCreateUserForm` collect
just username + password — `User.email` (`app/models.py`) is nullable and
non-unique (migration `4361e0a4c373_make_user_email_optional`, downgrades back to
`NOT NULL UNIQUE` if ever needed), and stays `NULL` for everyone except the seeded
admin, whose email is set/looked-up by `_seed_admin` (still keyed on `ADMIN_EMAIL`
for idempotent upserts — that's the one place email still matters). Because of this,
**login switched from email to username** (`LoginForm`/`auth.login` in
`app/forms.py`/`app/blueprints/auth.py`) — there'd be no way for a regular user to
log in by email once they no longer have one. The seeded admin logs in by username
too now (e.g. `admin`, not `admin@example.com`).

`/admin/users` now also has its own add/delete UI (`admin.create_user`/`admin.delete_user`,
`AdminCreateUserForm` in `app/forms.py`) — the admin can create accounts directly
(with an optional "Admin" checkbox, so promoting additional admins no longer needs
hand-editing the DB) and remove ones that were created in error (an admin can't delete
their own account). **Deletion now cascades**: `User.predictions` and the
`User.round_entries` backref (`app/models.py`) both carry `cascade="all,
delete-orphan"`, so removing a user takes their predictions, round entries *and*
opt-ins with them in one transaction — they disappear immediately from the players
grid (built from `RoundEntry.opted_in` rows), standings and pot calculations, with
no dangling rows or orphaned FK references left behind. (Earlier this was blocked
outright if the user had any activity, to avoid corrupting settled-round history -
cascading is the more useful behaviour the admin actually wants when cleaning up an
account, and leaderboards/pots are always derived fresh from `Prediction`/`RoundEntry`
so there's nothing "settled" to corrupt.)

### Display names — User.display_name, app/blueprints/auth.py:profile
Login identity (`username`) and public identity (`display_name`) are deliberately
separate columns: `username` is collected at sign-up purely to log in and is never
shown to anyone but its owner (and the admin, who needs it to manage accounts);
`display_name` is what every other player sees — players grid, leaderboard, pot/
finance rows, avatar initials (`main._avatar`), and the navbar. It's required
(`nullable=False`) and collected alongside username at registration
(`RegistrationForm`/`auth.register`) and admin user creation (`AdminCreateUserForm`/
`admin.create_user`); existing accounts were backfilled to `display_name = username`
by migration `760005299371_add_user_display_name` (added nullable, backfilled via
`UPDATE`, then tightened to `NOT NULL` - the standard 3-step pattern for adding a
required column to a populated table). Players can change theirs any time on the new
`/auth/profile` page (`ProfileForm`, `auth/profile.html`) without touching their
login credential. Sort order in the places that show it (`main.players`,
`leaderboards.round_leaderboard`/`tournament_standings`) was switched from
`User.username` to `User.display_name` so the on-screen order matches what's
displayed.

### Per-round stake — Round.stake_amount, app/finance.py
The pot used to be a hardcoded `STAKE_AMOUNT = £5.00` constant; it's now
`Round.stake_amount` (`Numeric(8, 2)`, set by the admin on the "create draft round"
form - `admin.create_round` parses and validates it as a positive `Decimal`,
defaulting to `DEFAULT_STAKE_AMOUNT` (£5.00, in `app/models.py`) when left blank).
Every pot/settlement calculation (`finance.round_pot`/`round_financial_summary`,
`main.round_opt_in`'s confirmation flash, `predictions.my_predictions`'s opt-in nudge,
the players-grid opt-in button/hint) now reads the *round's* stake rather than a
global constant, so different rounds (e.g. knockout vs. group stage) can carry
different buy-ins. Migration `b8059953b427_add_round_stake_amount` backfills existing
rounds with the old £5.00 default via a `server_default` that's then dropped, so the
column can be `NOT NULL` without disturbing rounds created before this change.

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
- `web: gunicorn run:app --workers 1`
- `release: flask db upgrade && flask seed-admin` — runs migrations and (re)provisions
  the superuser on every deploy on Heroku-style platforms.

**Railway does not execute the Procfile's `release:` line** (there's no
`railway.json`/`railway.toml` configuring a pre-deploy/release command either) — so on
Railway the schema was never migrated and the admin user never seeded, which surfaced as
two symptoms after the first deploy: login 500s (no `users` row/table to authenticate
against) and a DB error logged near dashboard loads whose SQL fragment was
`fixtures.kickoff_at >= %(kickoff_at_1)s::TIMESTAMP WITHOUT TIME ZONE` — that's just
the (correct, naive-UTC, `TIMESTAMP WITHOUT TIME ZONE`-compatible) compiled query from
the scheduler's `_todays_fixtures` (`app/scheduler.py`, the only `Fixture.kickoff_at`
inequality query in the codebase, which the in-process scheduler runs immediately on
boot); the actual underlying error was "relation \"fixtures\" does not exist".

**Fix**: `app/__init__.py`'s `run_startup_tasks` now runs `flask_migrate.upgrade()` and
seeds/promotes the admin (via `_seed_admin`, shared with the `seed-admin` CLI command)
on every app boot, inside `with app.app_context()` and wrapped in try/except so a
not-yet-reachable DB logs instead of crashing the worker. Both operations are idempotent,
so this is safe to run on every boot regardless of platform — Railway, Heroku, or local.
The Procfile's `release:` line is left in place for Heroku-style platforms that do run it.

Set `DATABASE_URL` (Railway Postgres plugin provides this — `config.py` normalizes
`postgres://`/`postgresql://` → `postgresql+psycopg://` for the psycopg v3 driver,
see [[psycopg v3 driver]]), `SECRET_KEY`, `FOOTBALL_DATA_API_KEY`, and
`ADMIN_USERNAME`/`ADMIN_EMAIL`/`ADMIN_PASSWORD` as Railway environment variables.

### psycopg v3 driver — config._normalize_db_url
Switched from `psycopg2-binary` to `psycopg[binary]` (psycopg v3) because Railway's
runtime image lacks the system `libpq.so.5` that plain `psycopg2`/`postgresql://`
(which resolves to the psycopg2 dialect) dynamically links against, crashing on boot
with `ImportError: libpq.so.5: cannot open shared object file`. psycopg v3's binary
wheel bundles `libpq` itself. `_normalize_db_url` rewrites whatever Railway/Heroku-style
URL it's handed (`postgres://` or `postgresql://`) to `postgresql+psycopg://`, pinning
the dialect explicitly — the bare `postgresql://` scheme would otherwise resolve to the
psycopg2 dialect by default and reintroduce the crash.

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
