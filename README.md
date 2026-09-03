# Swiss Tourney Manager

A Django web app for running simplified Swiss-style chess tournaments. It supports tournament creation and editing, player management, Swiss pairings, result entry, standings with Sonneborn-Berger tie-breaks, performance-rating estimates, byes, withdrawals, tournament timing, and a large auto-refreshing spectator display.

## Requirements

- Python 3.11+
- pip

## Running locally

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
python manage.py migrate
python manage.py runserver
```

Open http://127.0.0.1:8000/ in your browser.

Each browser session gets its own private, temporary tournament workspace with no account required. Use **Start over** from the tournament list to delete the current workspace. Individual tournaments can also be deleted after confirming in the delete dialog.

## Features

- Create tournaments with a manually selected round count and edit the name or round count before the first round starts.
- Delete tournaments with a confirmation dialog.
- Add and edit players with optional initial ratings.
- Withdraw players from future rounds without deleting historical pairings.
- Generate simplified Swiss pairings by score group while avoiding rematches when possible.
- Assign rotating full-point byes to the lowest-scoring eligible player.
- Prefer color assignments that reduce color imbalance.
- Enter round results and block future round generation until the current round is complete.
- Show standings sorted by score, then Sonneborn-Berger.
- Track total tournament time from the first round until completion, capped at 24 hours.
- Show each completed round's exact elapsed time on the tournament overview.
- Show a final performance-rating estimate using the FIDE section 8.1.1 score-to-rating-difference table. Blank opponent ratings are treated as 1200 and byes are excluded.
- Spectator display view for showing pairings and standings on a large screen.

## Performance-rating estimate

The performance rating is shown on the standings page after a tournament is completed. For each player, the app averages opponents' initial ratings, treating a blank rating as 1200, then adds the FIDE lookup-table rating difference that corresponds to the player's score percentage. Exact percentages use the table entry directly; other percentages use the nearest table entry. Byes and players with no played games are excluded from this calculation.

This is a FIDE-inspired estimate for local tournament use, not an official FIDE rating calculation. Official FIDE ratings also depend on rated-player status, initial-rating rules, rating floors, rating-difference limits, development coefficients, and tournament reporting requirements.

## Pairing limitations

The pairing engine is intentionally simple. It groups players by score, searches for no-rematch pairings, and falls back to controlled rematches only when no valid non-rematch solution exists. It is not a full FIDE Dutch-system implementation.

## Running tests

```powershell
python manage.py test
```
