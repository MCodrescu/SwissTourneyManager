from dataclasses import dataclass

from .models import Pairing


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


def calculate_standings(tournament):
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
    )


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
