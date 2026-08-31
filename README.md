# Swiss Tourney Manager

A local-director Django web app for running simplified Swiss-style chess tournaments. It supports tournament creation, player management, Swiss pairings, result entry, standings with Sonneborn-Berger tie-breaks, byes, withdrawals, and a large auto-refreshing spectator display.

## Local setup

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
python manage.py migrate
python manage.py runserver
```

Open http://127.0.0.1:8000/.

## Features

- Create tournaments with a manually selected round count.
- Give each browser session a private, temporary tournament workspace with no account required.
- Add and edit players with optional initial ratings.
- Withdraw players from future rounds without deleting historical pairings.
- Generate simplified Swiss pairings by score group while avoiding rematches when possible.
- Assign rotating full-point byes to the lowest-scoring eligible player.
- Prefer color assignments that reduce color imbalance.
- Enter round results and block future round generation until the current round is complete.
- Show standings sorted by score, then Sonneborn-Berger.
- Use `/tournament/<id>/display/` for a projector-friendly standings display that refreshes every 15 seconds.

## Pairing limitations

The v1 pairing engine is intentionally simple. It groups players by score, searches for no-rematch pairings, and falls back to controlled rematches only when no valid non-rematch solution exists. It is not a full FIDE Dutch-system implementation.

## DigitalOcean App Platform

This project is ready for DigitalOcean App Platform with Gunicorn and WhiteNoise. App Platform can be connected to a new GitHub repository and configured to auto-deploy from `main`.

Build command:

```sh
pip install -r requirements.txt && python manage.py collectstatic --noinput
```

Run command:

```sh
gunicorn chess_tournament.wsgi --bind 0.0.0.0:8080
```

Environment variables:

- `DEBUG=False`
- `SECRET_KEY=<secure production secret>`
- `ALLOWED_HOSTS=.ondigitalocean.app,your-custom-domain.example`

## SQLite on App Platform

The app uses Django's default SQLite database. On DigitalOcean App Platform this database should be treated as ephemeral: data can reset on redeploys, restarts, or container replacement. That tradeoff is intentional for this v1 local-director use case. Use a managed PostgreSQL database later if tournament data must survive deployments.

## Anonymous Workspaces

Each browser session starts with an empty, private tournament list. A visitor can access only tournaments created in that same browser session; direct links to another visitor's tournament return `404`. Multiple tabs in one browser profile share a workspace. Use **Start over** from the tournament list to delete the current workspace immediately.

Browser sessions expire when the browser closes. The server removes expired session data and its tournaments when this command runs:

```sh
python manage.py purge_expired_workspaces
```

Schedule that command at least daily in a public deployment. Run it instead of `python manage.py clearsessions`, because it removes the associated tournament data before deleting expired session records.

## Verification

```powershell
python manage.py test
python manage.py check
python manage.py collectstatic --noinput
```
