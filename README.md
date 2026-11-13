# Swiss Tourney Manager

A Django web app for running simplified Swiss-style chess tournaments. It supports tournament creation and editing, player management, Swiss pairings, result entry, standings with Sonneborn-Berger tie-breaks, performance-rating estimates, byes, withdrawals, tournament timing, and a large auto-refreshing spectator display.

## Requirements

- Python 3.11-3.13 (Django 5.1 does not support Python 3.14)
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

On the tournament list, enter a **Tournament director key** and select **Save / Load tournaments**. A new key creates a saved workspace; entering the same key from another browser reloads its tournaments, players, rounds, and results. Keys are case-insensitive and accept 1-31 letters, numbers, hyphens, or underscores. One key can identify multiple tournaments. Changes save automatically as you submit them.

Anyone who knows or guesses a key can view, change, or delete its tournaments. This is an identifier, not secure authentication; use a distinct key to avoid accidental sharing. Never use your Supabase database password as a director key.

Before entering a key, tournaments belong to a temporary browser workspace. Entering a key moves those temporary tournaments and their player directory into the saved workspace. If both directories have the same player name, the temporary directory's rating takes precedence. The key field and **Save / Load tournaments** button stay disabled while a workspace is active. Select **Leave director workspace** to enable key entry again and switch workspaces; leaving does not delete or move saved tournaments or their directory. Saved workspaces survive browser-session expiry and `purge_expired_workspaces`. **Start over** permanently deletes all tournaments and the player directory under the current key, including for other directors using that key. Individual tournaments can also be deleted after confirming in the delete dialog; their saved players remain in the directory.

## Player directory

Select **Player directory** on the tournament list, or **Manage player directory** on a tournament's roster, to add players, edit saved player names and ELO ratings, or remove players from future selection. The **Add player** form saves a name and optional ELO rating without requiring a tournament or adding the player to any roster. Duplicate names in the same directory are rejected; use **Edit** to update an existing saved player. Removal requires confirmation. The directory belongs to the current director workspace and is available across browsers using the same director key.

Directory edits and removals never change existing tournament rosters, withdrawal status, pairings, results, or standings. The roster's **Existing player** dropdown copies the directory's current name and rating into a new tournament entry. Players already on that roster by name are excluded. Editing a player on a tournament roster changes only that tournament entry, not the directory.

Adding a new player to a tournament, including a late entry to posted pairings, saves their name and rating in the directory. Adding the same name again through a tournament updates its saved rating. After removing a directory entry, you can save that player again using the directory's **Add player** form or by adding them as a new player to a tournament. Temporary directories expire with their browser sessions; use a director key to keep yours.

Run `python manage.py migrate` after updating. The directory migration imports players from existing tournaments once, using the latest-added tournament entry for each name within each workspace. Existing tournament data remains unchanged.

## Supabase Database

1. Create a free Supabase project and keep its database password in your deployment's secret settings.
2. In the Supabase **Connect** dialog, select the PostgreSQL **Session pooler** connection string (port `5432`). This supports IPv4 hosts. Use the connection string Supabase provides; its username usually looks like `postgres.PROJECT_REF`. URL-encode special characters in the password. Do not use the Supabase project HTTPS URL or an API key.
3. Set `DATABASE_URL` to that connection string in your Django server's environment. Set `SECRET_KEY` to a random secret, `DEBUG=False`, and `ALLOWED_HOSTS` to your deployed hostname. The values in [.env.example](.env.example) are placeholders; Django does not automatically load this file. For local testing, export the variables in your terminal before running Django. Do not commit real credentials.
4. Install dependencies and run migrations against Supabase:

	```bash
	pip install -r requirements.txt
	python manage.py migrate
	python manage.py collectstatic --noinput
	```

5. Restart your Django server with the same environment. Create a tournament under a director key, then enter that key in another browser to verify that it reloads.

Django connects directly to PostgreSQL over TLS; no Supabase JavaScript client or API key is needed. Keep the database URL server-side. If this project only uses Django, disable the Supabase Data API in the project's API settings so Django's tables are not exposed through that separate API. Director keys do not provide database access controls for the Supabase Data API.

Without `DATABASE_URL`, the app continues to use local SQLite. Switching databases does not copy existing SQLite data. To transfer existing local tournaments, first enter a director key for them, then export before setting `DATABASE_URL`:

```bash
python manage.py dumpdata tournaments --indent 2 --output tournaments-backup.json
```

After setting `DATABASE_URL` and running migrations, import into an empty Supabase tournament database:

```bash
python manage.py loaddata tournaments-backup.json
```

The backup contains player names and director identifiers; keep it private and out of version control. Supabase's free plan has storage limits and may pause inactive projects; check the dashboard before an event. Persistent storage is not a backup, so keep separate exports for important tournaments.

## Features

- Create tournaments with a manually selected round count and edit the name or round count before the first round starts.
- Delete tournaments with a confirmation dialog.
- Add and edit players with optional initial ratings.
- Manage a workspace-level player directory with name/rating editing and confirmed removal, without changing tournament history.
- Reuse saved players in the same director workspace through the roster's **Existing player** dropdown. The directory's current name and rating are copied into the new tournament; past results and withdrawal status remain unchanged.
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
