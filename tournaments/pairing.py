from dataclasses import dataclass, field


@dataclass(frozen=True)
class PlayerCard:
    id: int
    name: str
    score: float = 0.0
    opponents: frozenset[int] = field(default_factory=frozenset)
    colors: tuple[str, ...] = field(default_factory=tuple)
    bye_count: int = 0
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
    return min(
        players,
        key=lambda player: (player.bye_count, player.score, player.initial_rating or 0, player.name.lower(), player.id),
    )


def _pair_pool(players):
    if not players:
        return []

    ordered = sorted(players, key=_player_sort_key)
    pairings = []
    floaters = []
    for group in _score_groups(ordered):
        paired, floaters = _greedy_pair(floaters + group, allow_rematches=False)
        pairings.extend(paired)

    if floaters:
        paired, floaters = _greedy_pair(floaters, allow_rematches=True)
        pairings.extend(paired)

    if floaters:
        raise ValueError('Unable to generate pairings for this player pool.')

    return pairings


def _score_groups(ordered_players):
    groups = []
    current_group = []
    current_score = None
    for player in ordered_players:
        if current_group and player.score != current_score:
            groups.append(current_group)
            current_group = []
        current_group.append(player)
        current_score = player.score
    if current_group:
        groups.append(current_group)
    return groups


def _greedy_pair(pool, allow_rematches):
    remaining = list(pool)
    pairings = []
    unpaired = []
    while remaining:
        first = remaining.pop(0)
        opponent = _find_opponent(first, remaining, allow_rematches)
        if opponent is None:
            unpaired.append(first)
            continue
        remaining.remove(opponent)
        pairings.append(_make_pairing(first, opponent))
    return pairings, unpaired


def _find_opponent(first, candidates, allow_rematches):
    for candidate in candidates:
        if allow_rematches or candidate.id not in first.opponents:
            return candidate
    return None


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


def validate_no_duplicate_players(pairings):
    seen = []
    for pairing in pairings:
        seen.extend(player_id for player_id in (pairing.white_id, pairing.black_id, pairing.bye_id) if player_id is not None)
    return len(seen) == len(set(seen))
