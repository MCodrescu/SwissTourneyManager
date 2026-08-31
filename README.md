# Swiss Tourney Manager

A Django web app for running simplified Swiss-style chess tournaments. It supports tournament creation, player management, Swiss pairings, result entry, standings with Sonneborn-Berger tie-breaks, byes, withdrawals, and a large auto-refreshing spectator display.

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

Each browser session gets its own private, temporary tournament workspace with no account required. Use **Start over** from the tournament list to delete the current workspace.

## Features

- Create tournaments with a manually selected round count.
- Add and edit players with optional initial ratings.
- Withdraw players from future rounds without deleting historical pairings.
- Generate simplified Swiss pairings by score group while avoiding rematches when possible.
- Assign rotating full-point byes to the lowest-scoring eligible player.
- Prefer color assignments that reduce color imbalance.
- Enter round results and block future round generation until the current round is complete.
- Show standings sorted by score, then Sonneborn-Berger.
- Spectator display view for showing pairings and standings on a large screen.

## Pairing limitations

The pairing engine is intentionally simple. It groups players by score, searches for no-rematch pairings, and falls back to controlled rematches only when no valid non-rematch solution exists. It is not a full FIDE Dutch-system implementation.

## Running tests

```powershell
python manage.py test
```
