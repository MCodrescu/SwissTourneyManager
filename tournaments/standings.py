from dataclasses import dataclass

from .models import Pairing


# FIDE Rating Regulations, section 8.1.1: fractional score to rating difference.
FIDE_SCORE_TO_DIFFERENCE = {
    score / 100: difference
    for score, difference in enumerate([
        -800, -677, -589, -538, -501, -470, -444, -422, -401, -383,
        -366, -351, -336, -322, -309, -296, -284, -273, -262, -251,
        -240, -230, -220, -211, -202, -193, -184, -175, -166, -158,
        -149, -141, -133, -125, -117, -110, -102, -95, -87, -80,
        -72, -65, -57, -50, -43, -36, -29, -21, -14, -7,
        0, 7, 14, 21, 29, 36, 43, 50, 57, 65,
        72, 80, 87, 95, 102, 110, 117, 125, 133, 141,
        149, 158, 166, 175, 184, 193, 202, 211, 220, 230,
        240, 251, 262, 273, 284, 296, 309, 322, 336, 351,
        366, 383, 401, 422, 444, 470, 501, 538, 589, 677, 800,
    ])
}


@dataclass(frozen=True)
class StandingRow:
    player: object
    score: float
    sonneborn_berger: float
    games_played: int
    wins: int
    draws: int
    losses: int
    byes: int
    color_history: tuple[str, ...]
    opponent_ids: tuple[int, ...]
    performance_rating: int | None


def calculate_standings(tournament):
    """Build standings with scores, tie-breaks, records, and performance ratings."""
    players = list(tournament.players.all())
    pairings = list(
        Pairing.objects.filter(round__tournament=tournament, result__in=Pairing.completed_results())
        .select_related('player_white', 'player_black', 'bye_player', 'round')
        .order_by('round__round_number', 'id')
    )
    scores = _score_players(players, pairings)

    rows = [_build_standing_row(player, pairings, scores) for player in players]
    return sorted(rows, key=lambda row: (-row.score, -row.sonneborn_berger, row.player.name.lower()))


def _score_players(players, pairings):
    scores = {player.id: 0.0 for player in players}

    for pairing in pairings:
        for player in _players_in_pairing(pairing):
            scores[player.id] += pairing.score_for(player)
    return scores


def _build_standing_row(player, pairings, scores):
    record = {'wins': 0, 'draws': 0, 'losses': 0, 'byes': 0}
    sb = 0.0
    colors = []
    opponent_ids = []

    for pairing in pairings:
        if not _has_player(pairing, player):
            continue
        if pairing.result == Pairing.ResultChoices.BYE and pairing.bye_player_id == player.id:
            record['byes'] += 1
            continue

        opponent = pairing.opponent_for(player)
        if opponent is None:
            continue

        opponent_ids.append(opponent.id)
        _append_color(colors, pairing, player)
        sb += _update_record_and_sb(record, pairing.score_for(player), scores[opponent.id])

    games_played = record['wins'] + record['draws'] + record['losses']
    return StandingRow(
        player=player,
        score=scores[player.id],
        sonneborn_berger=sb,
        games_played=games_played,
        wins=record['wins'],
        draws=record['draws'],
        losses=record['losses'],
        byes=record['byes'],
        color_history=tuple(colors),
        opponent_ids=tuple(opponent_ids),
        performance_rating=_performance_rating(player, pairings, record, scores),
    )


def _performance_rating(player, pairings, record, scores):
    """Estimate performance using average opponent rating and the FIDE lookup table."""
    opponents = []
    for pairing in pairings:
        if not _has_player(pairing, player) or pairing.result == Pairing.ResultChoices.BYE:
            continue
        opponent = pairing.opponent_for(player)
        if opponent is not None:
            opponents.append(opponent)

    if not opponents:
        return None

    average_opponent_rating = sum(
        opponent.initial_rating if opponent.initial_rating is not None else 1200
        for opponent in opponents
    ) / len(opponents)
    score_percentage = (record['wins'] + record['draws'] * 0.5) / len(opponents)
    nearest_score = min(
        FIDE_SCORE_TO_DIFFERENCE,
        key=lambda table_score: abs(table_score - score_percentage),
    )
    adjustment = FIDE_SCORE_TO_DIFFERENCE[nearest_score]
    return round(average_opponent_rating + adjustment)


def _append_color(colors, pairing, player):
    if pairing.player_white_id == player.id:
        colors.append('W')
    elif pairing.player_black_id == player.id:
        colors.append('B')


def _update_record_and_sb(record, player_score, opponent_score):
    if player_score > 0.75:
        record['wins'] += 1
        return opponent_score
    if player_score > 0.25:
        record['draws'] += 1
        return opponent_score * 0.5
    record['losses'] += 1
    return 0.0


def _players_in_pairing(pairing):
    return [player for player in (pairing.player_white, pairing.player_black, pairing.bye_player) if player is not None]


def _has_player(pairing, player):
    return player.id in {pairing.player_white_id, pairing.player_black_id, pairing.bye_player_id}
