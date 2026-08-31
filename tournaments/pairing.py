from dataclasses import dataclass, field
from itertools import combinations


@dataclass(frozen=True)
class PlayerCard:
    id: int
    name: str
    score: float = 0.0
    opponents: frozenset[int] = field(default_factory=frozenset)
    colors: tuple[str, ...] = field(default_factory=tuple)
    had_bye: bool = False
    initial_rating: int | None = None

    @property
    def color_balance(self):
        return self.colors.count('W') - self.colors.count('B')


@dataclass(frozen=True)
class PairingCard:
    white_id: int | None = None
    black_id: int | None = None
    bye_id: int | None = None

    @property
    def is_bye(self):
        return self.bye_id is not None


def generate_pairings(players):
    pool = sorted(players, key=_player_sort_key)
    bye = _select_bye(pool) if len(pool) % 2 else None
    if bye is not None:
        pool = [player for player in pool if player.id != bye.id]

    pairings = _pair_pool(pool)
    if bye is not None:
        pairings.append(PairingCard(bye_id=bye.id))
    return pairings


def _player_sort_key(player):
    rating = player.initial_rating if player.initial_rating is not None else 0
    return (-player.score, -rating, player.name.lower(), player.id)


def _select_bye(players):
    candidates = sorted(players, key=lambda player: (player.score, player.had_bye, player.initial_rating or 0, player.name.lower(), player.id))
    for player in candidates:
        if not player.had_bye:
            return player
    return candidates[0]


def _pair_pool(players):
    if not players:
        return []

    search_players = tuple(players)
    solution = _search_pairings(search_players)
    if solution is None:
        solution = _search_pairings(search_players, allow_rematches=True)
    if solution is None:
        raise ValueError('Unable to generate pairings for this player pool.')
    return solution


def _search_pairings(players, allow_rematches=False):
    if not players:
        return []

    first = players[0]
    best = None
    best_cost = None

    for index in range(1, len(players)):
        second = players[index]
        if not allow_rematches and second.id in first.opponents:
            continue

        remaining = players[1:index] + players[index + 1:]
        rest = _search_pairings(remaining, allow_rematches)
        if rest is None:
            continue

        card = _make_pairing(first, second)
        candidate = [card] + rest
        cost = _pairing_cost(first, second, card) + sum(_card_cost(players, existing) for existing in rest)
        if best is None or cost < best_cost:
            best = candidate
            best_cost = cost

    return best


def _make_pairing(first, second):
    first_as_white = _color_assignment_cost(first, second, first_white=True)
    second_as_white = _color_assignment_cost(first, second, first_white=False)
    if first_as_white <= second_as_white:
        return PairingCard(white_id=first.id, black_id=second.id)
    return PairingCard(white_id=second.id, black_id=first.id)


def _color_assignment_cost(first, second, first_white):
    first_next = 1 if first_white else -1
    second_next = -1 if first_white else 1
    return abs(first.color_balance + first_next) + abs(second.color_balance + second_next)


def _pairing_cost(first, second, card):
    score_gap = abs(first.score - second.score) * 10
    return score_gap + _card_cost((first, second), card)


def _card_cost(players, card):
    if card.is_bye:
        return 0
    by_id = {player.id: player for player in players}
    white = by_id.get(card.white_id)
    black = by_id.get(card.black_id)
    if white is None or black is None:
        return 0
    return abs(white.color_balance + 1) + abs(black.color_balance - 1)


def validate_no_duplicate_players(pairings):
    seen = []
    for pairing in pairings:
        seen.extend(player_id for player_id in (pairing.white_id, pairing.black_id, pairing.bye_id) if player_id is not None)
    return len(seen) == len(set(seen))
