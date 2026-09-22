from django.contrib import messages
from django.conf import settings
from django.db import IntegrityError, transaction
from django.forms import modelformset_factory
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_GET, require_POST

from .forms import PairingResultForm, PlayerForm, TournamentForm
from .models import Pairing, Player, Round, Tournament
from .pairing import PlayerCard, generate_pairings
from .standings import calculate_standings

PLAYER_LIST_ROUTE = 'tournaments:player_list'
ROUND_DETAIL_ROUTE = 'tournaments:round_detail'


def _workspace_key(request):
	if not request.session.session_key:
		request.session.create()
	return request.session.session_key


def _workspace_tournament(request, tournament_id):
	return get_object_or_404(
		Tournament,
		id=tournament_id,
		workspace_key=_workspace_key(request),
	)


@require_GET
def tournament_list(request):
	tournaments = Tournament.objects.filter(workspace_key=_workspace_key(request))
	return render(request, 'tournaments/tournament_list.html', {'tournaments': tournaments})


def tournament_create(request):
	form = TournamentForm(request.POST or None)
	if request.method == 'POST' and form.is_valid():
		workspace_key = _workspace_key(request)
		if Tournament.objects.filter(workspace_key=workspace_key).count() >= settings.WORKSPACE_TOURNAMENT_LIMIT:
			return HttpResponse('This workspace has reached its tournament limit.', status=429)
		tournament = form.save(commit=False)
		tournament.workspace_key = workspace_key
		tournament.save()
		return redirect('tournaments:tournament_detail', tournament_id=tournament.id)
	return render(request, 'tournaments/form.html', {'form': form, 'title': 'New tournament'})


def tournament_edit(request, tournament_id):
	tournament = _workspace_tournament(request, tournament_id)
	form = TournamentForm(request.POST or None, instance=tournament)
	if request.method == 'POST' and form.is_valid():
		form.save()
		messages.success(request, f'{tournament.name} updated.')
		return redirect('tournaments:tournament_detail', tournament_id=tournament.id)
	return render(request, 'tournaments/form.html', {'form': form, 'title': f'Edit {tournament.name}', 'tournament': tournament})


@require_POST
def tournament_delete(request, tournament_id):
	tournament = _workspace_tournament(request, tournament_id)
	tournament_name = tournament.name
	tournament.delete()
	messages.success(request, f'{tournament_name} deleted.')
	return redirect('tournaments:tournament_list')


@require_GET
def tournament_detail(request, tournament_id):
	tournament = _workspace_tournament(request, tournament_id)
	rounds = tournament.rounds.prefetch_related('pairings')
	standings = calculate_standings(tournament)
	active_player_count = tournament.players.filter(is_withdrawn=False).count()
	rounds_remaining = max(tournament.num_rounds - tournament.current_round, 0)
	completed_round_count = rounds.filter(is_completed=True).count()
	can_complete_tournament = (
		tournament.is_active
		and tournament.current_round == tournament.num_rounds
		and rounds.count() == tournament.num_rounds
		and not rounds.filter(is_completed=False).exists()
	)
	can_post_pairings = (
		_active_round(tournament) is None
		and tournament.current_round < tournament.num_rounds
		and (tournament.current_round > 0 or active_player_count >= 2)
	)
	return render(request, 'tournaments/tournament_detail.html', {
		'tournament': tournament,
		'rounds': rounds,
		'standings': standings,
		'active_player_count': active_player_count,
		'rounds_remaining': rounds_remaining,
		'completed_round_count': completed_round_count,
		'can_complete_tournament': can_complete_tournament,
		'can_post_pairings': can_post_pairings,
		'pending_round': _pending_round(tournament),
	})


def player_list(request, tournament_id):
	tournament = _workspace_tournament(request, tournament_id)
	players = tournament.players.all()
	active_player_count = players.filter(is_withdrawn=False).count()
	pending_round = _pending_round(tournament)
	roster_locked = not tournament.is_active or _active_round(tournament) is not None
	form = PlayerForm(request.POST or None)
	if request.method == 'POST':
		if roster_locked:
			messages.error(request, 'Players cannot be added while a round is in progress.')
			return redirect(PLAYER_LIST_ROUTE, tournament_id=tournament.id)
		if form.is_valid():
			if players.count() >= settings.TOURNAMENT_PLAYER_LIMIT:
				return HttpResponse('This tournament has reached its player limit.', status=429)
			_add_player(request, tournament, form, pending_round)
			return redirect(PLAYER_LIST_ROUTE, tournament_id=tournament.id)
	return render(request, 'tournaments/player_list.html', {
		'tournament': tournament,
		'players': players,
		'active_player_count': active_player_count,
		'roster_locked': roster_locked,
		'pending_round': pending_round,
		'form': form,
	})


def player_edit(request, tournament_id, player_id):
	tournament = _workspace_tournament(request, tournament_id)
	player = get_object_or_404(Player, id=player_id, tournament=tournament)
	form = PlayerForm(request.POST or None, instance=player)
	if request.method == 'POST' and form.is_valid():
		form.save()
		return redirect(PLAYER_LIST_ROUTE, tournament_id=tournament.id)
	return render(request, 'tournaments/form.html', {'form': form, 'title': f'Edit {player.name}', 'tournament': tournament})


@require_POST
def player_withdraw(request, tournament_id, player_id):
	tournament = _workspace_tournament(request, tournament_id)
	player = get_object_or_404(Player, id=player_id, tournament=tournament)
	with transaction.atomic():
		player.withdraw()
		pending_round = _pending_round(tournament)
		if pending_round is not None:
			_reseat_unpaired_players(pending_round, tournament)
	messages.info(request, f'{player.name} withdrawn from future pairings.')
	return redirect(PLAYER_LIST_ROUTE, tournament_id=tournament.id)


@require_POST
def complete_tournament(request, tournament_id):
	tournament = _workspace_tournament(request, tournament_id)
	all_rounds_complete = (
		tournament.current_round == tournament.num_rounds
		and tournament.rounds.count() == tournament.num_rounds
		and not tournament.rounds.filter(is_completed=False).exists()
	)
	if tournament.is_active and all_rounds_complete:
		tournament.is_active = False
		tournament.end_time = timezone.now()
		tournament.save(update_fields=['is_active', 'end_time'])
		messages.success(request, f'{tournament.name} completed.')
		return redirect('tournaments:standings', tournament_id=tournament.id)
	else:
		messages.error(request, 'Complete all scheduled rounds before completing the tournament.')
	return redirect('tournaments:tournament_detail', tournament_id=tournament.id)


@require_POST
def end_tournament_early(request, tournament_id):
	tournament = _workspace_tournament(request, tournament_id)
	if tournament.is_active:
		tournament.is_active = False
		tournament.end_time = timezone.now()
		tournament.save(update_fields=['is_active', 'end_time'])
		messages.success(request, f'{tournament.name} ended early.')
		return redirect('tournaments:standings', tournament_id=tournament.id)
	messages.error(request, 'This tournament has already ended.')
	return redirect('tournaments:tournament_detail', tournament_id=tournament.id)


@require_POST
def generate_round(request, tournament_id):
	tournament = _workspace_tournament(request, tournament_id)
	if tournament.current_round >= tournament.num_rounds:
		messages.error(request, 'This tournament has already reached its scheduled round count.')
		return redirect('tournaments:tournament_detail', tournament_id=tournament.id)

	blocked = _blocking_round_redirect(request, tournament)
	if blocked:
		return blocked

	active_players = list(tournament.players.filter(is_withdrawn=False))
	if len(active_players) < 2:
		messages.error(request, 'Add at least two active players before generating pairings.')
		return redirect(PLAYER_LIST_ROUTE, tournament_id=tournament.id)

	try:
		with transaction.atomic():
			# lock the row so a concurrent request can't generate a duplicate round
			tournament = Tournament.objects.select_for_update().get(
				id=tournament_id,
				workspace_key=_workspace_key(request),
			)
			if tournament.current_round >= tournament.num_rounds:
				messages.error(request, 'This tournament has already reached its scheduled round count.')
				return redirect('tournaments:tournament_detail', tournament_id=tournament.id)
			blocked = _blocking_round_redirect(request, tournament)
			if blocked:
				return blocked

			round_number = tournament.current_round + 1
			round_obj = Round.objects.create(tournament=tournament, round_number=round_number)
			player_cards = [_player_card(player, tournament) for player in active_players]
			cards = generate_pairings(player_cards)
			players_by_id = {player.id: player for player in active_players}
			_create_pairings(round_obj, cards, players_by_id, _free_board_numbers(set()))
	except IntegrityError:
		messages.error(request, 'Another request already generated this round. Please refresh and try again.')
		return redirect('tournaments:tournament_detail', tournament_id=tournament.id)

	messages.success(request, f'Round {round_number} pairings posted. Start the round once every board is seated.')
	return redirect(ROUND_DETAIL_ROUTE, tournament_id=tournament.id, round_id=round_obj.id)


@require_POST
def start_round(request, tournament_id, round_id):
	tournament = _workspace_tournament(request, tournament_id)
	round_obj = get_object_or_404(Round, id=round_id, tournament=tournament)

	with transaction.atomic():
		tournament = Tournament.objects.select_for_update().get(
			id=tournament_id,
			workspace_key=_workspace_key(request),
		)
		round_obj.refresh_from_db()
		if round_obj.is_started:
			messages.error(request, f'Round {round_obj.round_number} has already started.')
			return redirect(ROUND_DETAIL_ROUTE, tournament_id=tournament.id, round_id=round_obj.id)
		if not round_obj.pairings.exists():
			messages.error(request, 'There are no pairings to start.')
			return redirect(ROUND_DETAIL_ROUTE, tournament_id=tournament.id, round_id=round_obj.id)

		now = timezone.now()
		round_obj.is_started = True
		round_obj.started_at = now
		round_obj.save(update_fields=['is_started', 'started_at'])

		update_fields = ['current_round']
		if tournament.current_round == 0:
			tournament.start_time = now
			update_fields.append('start_time')
		tournament.current_round = round_obj.round_number
		tournament.save(update_fields=update_fields)

	messages.success(request, f'Round {round_obj.round_number} started. Clocks are running.')
	return redirect(ROUND_DETAIL_ROUTE, tournament_id=tournament.id, round_id=round_obj.id)


@require_POST
def pairing_remove_player(request, tournament_id, round_id, player_id):
	"""Withdraw a player from a posted-but-unstarted round and re-seat whoever is left without an opponent."""
	tournament = _workspace_tournament(request, tournament_id)
	round_obj = get_object_or_404(Round, id=round_id, tournament=tournament)
	player = get_object_or_404(Player, id=player_id, tournament=tournament)

	if round_obj.is_started:
		messages.error(request, 'This round has already started. Withdraw the player from the roster instead.')
		return redirect(ROUND_DETAIL_ROUTE, tournament_id=tournament.id, round_id=round_obj.id)

	try:
		with transaction.atomic():
			player.withdraw()
			moved = _reseat_unpaired_players(round_obj, tournament)
	except ValueError:
		messages.error(request, 'Could not re-pair the remaining players automatically.')
		return redirect(ROUND_DETAIL_ROUTE, tournament_id=tournament.id, round_id=round_obj.id)

	if moved:
		messages.info(request, f'{player.name} removed. {moved} player(s) moved to open boards.')
	else:
		messages.info(request, f'{player.name} removed from the pairings.')
	return redirect(ROUND_DETAIL_ROUTE, tournament_id=tournament.id, round_id=round_obj.id)


@require_POST
def round_add_player(request, tournament_id, round_id):
	"""Register a late entry and slot them into the open seats of a posted-but-unstarted round."""
	tournament = _workspace_tournament(request, tournament_id)
	round_obj = get_object_or_404(Round, id=round_id, tournament=tournament)

	if round_obj.is_started:
		messages.error(request, 'This round has already started. Add the player before posting the next pairings.')
		return redirect(ROUND_DETAIL_ROUTE, tournament_id=tournament.id, round_id=round_obj.id)
	if tournament.players.count() >= settings.TOURNAMENT_PLAYER_LIMIT:
		return HttpResponse('This tournament has reached its player limit.', status=429)

	form = PlayerForm(request.POST)
	if form.is_valid():
		_add_player(request, tournament, form, round_obj)
	else:
		messages.error(request, 'Enter a valid player name and rating.')
	return redirect(ROUND_DETAIL_ROUTE, tournament_id=tournament.id, round_id=round_obj.id)


def round_detail(request, tournament_id, round_id):
	tournament = _workspace_tournament(request, tournament_id)
	round_obj = get_object_or_404(Round.objects.prefetch_related('pairings'), id=round_id, tournament=tournament)

	if not round_obj.is_started:
		return render(request, 'tournaments/round_detail.html', {
			'tournament': tournament,
			'round': round_obj,
			'formset': None,
			'board_pairings': round_obj.pairings.exclude(result=Pairing.ResultChoices.BYE),
			'add_player_form': PlayerForm(),
		})

	editable_pairings = round_obj.pairings.exclude(result=Pairing.ResultChoices.BYE)
	result_formset = modelformset_factory(Pairing, form=PairingResultForm, extra=0)
	formset = result_formset(request.POST or None, queryset=editable_pairings)

	if request.method == 'POST' and not round_obj.is_completed and formset.is_valid():
		with transaction.atomic():
			formset.save()
			if not round_obj.pairings.filter(result=Pairing.ResultChoices.PENDING).exists():
				round_obj.is_completed = True
				round_obj.completed_at = timezone.now()
				round_obj.save(update_fields=['is_completed', 'completed_at'])
				messages.success(request, f'Round {round_obj.round_number} completed.')
				return redirect('tournaments:tournament_detail', tournament_id=tournament.id)
		messages.error(request, 'Enter all board results before completing the round.')

	return render(request, 'tournaments/round_detail.html', {'tournament': tournament, 'round': round_obj, 'formset': formset})


@require_GET
def standings_view(request, tournament_id):
	tournament = _workspace_tournament(request, tournament_id)
	standings = calculate_standings(tournament)
	return render(request, 'tournaments/standings.html', {'tournament': tournament, 'standings': standings})


@require_POST
def workspace_reset(request):
	Tournament.objects.filter(workspace_key=_workspace_key(request)).delete()
	request.session.flush()
	messages.success(request, 'Your workspace has been cleared.')
	return redirect('tournaments:tournament_list')


def _pending_round(tournament):
	"""The most recent round whose pairings are posted but which has not been started."""
	return tournament.rounds.filter(is_started=False).order_by('-round_number').first()


def _active_round(tournament):
	"""The round currently being played, if any."""
	return tournament.rounds.filter(is_started=True, is_completed=False).order_by('-round_number').first()


def _add_player(request, tournament, form, pending_round):
	"""Save a new player, seating them in the pending round's open slot when there is one."""
	name = form.cleaned_data['name']
	if tournament.players.filter(name=name).exists():
		messages.error(request, f'{name} is already on the roster.')
		return None

	with transaction.atomic():
		player = form.save(commit=False)
		player.tournament = tournament
		player.save()
		if pending_round is not None:
			_reseat_unpaired_players(pending_round, tournament)

	if pending_round is None:
		messages.success(request, f'Added {player.name}.')
	else:
		messages.success(request, f'Added {player.name} to the round {pending_round.round_number} pairings.')
	return player


def _blocking_round_redirect(request, tournament):
	"""Redirect response when an existing round must be dealt with before pairing a new one."""
	latest_round = tournament.rounds.order_by('-round_number').first()
	if latest_round is None or latest_round.is_completed:
		return None
	if latest_round.is_started:
		messages.error(request, 'Complete the current round before generating another one.')
	else:
		messages.error(request, f'Round {latest_round.round_number} pairings are already posted.')
	return redirect(ROUND_DETAIL_ROUTE, tournament_id=tournament.id, round_id=latest_round.id)


def _free_board_numbers(used_boards):
	"""Yield board numbers in ascending order, skipping ones that are still occupied."""
	number = 1
	while True:
		if number not in used_boards:
			yield number
		number += 1


def _create_pairings(round_obj, cards, players_by_id, board_numbers):
	for card in cards:
		if card.is_bye:
			Pairing.objects.create(
				round=round_obj,
				bye_player=players_by_id[card.bye_id],
				result=Pairing.ResultChoices.BYE,
			)
		else:
			Pairing.objects.create(
				round=round_obj,
				player_white=players_by_id[card.white_id],
				player_black=players_by_id[card.black_id],
				board_number=next(board_numbers),
			)


def _reseat_unpaired_players(round_obj, tournament):
	"""Drop pairings containing inactive players and re-seat the leftovers on open boards.

	Boards that still have both players keep their existing board number so the rest of the
	hall does not have to move.
	"""
	active_players = list(tournament.players.filter(is_withdrawn=False))
	players_by_id = {player.id: player for player in active_players}

	used_boards = set()
	seated_ids = set()
	bye_pairing = None

	for pairing in round_obj.pairings.all():
		occupants = [pid for pid in (pairing.player_white_id, pairing.player_black_id, pairing.bye_player_id) if pid]
		if any(pid not in players_by_id for pid in occupants):
			pairing.delete()
			continue
		if pairing.bye_player_id:
			bye_pairing = pairing
			continue
		seated_ids.update(occupants)
		used_boards.add(pairing.board_number)

	unpaired = [player for player in active_players if player.id not in seated_ids and (bye_pairing is None or player.id != bye_pairing.bye_player_id)]
	if not unpaired:
		return 0

	# an odd leftover group can only be resolved by pulling the current bye player back into play
	if len(unpaired) % 2 and bye_pairing is not None:
		unpaired.append(players_by_id[bye_pairing.bye_player_id])
		bye_pairing.delete()

	cards = generate_pairings([_player_card(player, tournament) for player in unpaired])
	_create_pairings(round_obj, cards, players_by_id, _free_board_numbers(used_boards))
	return len(unpaired)


def _player_card(player, tournament):
	pairings = Pairing.objects.filter(
		round__tournament=tournament,
		result__in=Pairing.completed_results(),
	).filter(
		models_q_for_player(player)
	).select_related('player_white', 'player_black')
	opponents = []
	colors = []
	bye_count = 0
	score = 0.0

	for pairing in pairings:
		score += pairing.score_for(player)
		if pairing.result == Pairing.ResultChoices.BYE and pairing.bye_player_id == player.id:
			bye_count += 1
			continue
		opponent = pairing.opponent_for(player)
		if opponent:
			opponents.append(opponent.id)
		if pairing.player_white_id == player.id:
			colors.append('W')
		elif pairing.player_black_id == player.id:
			colors.append('B')

	return PlayerCard(
		id=player.id,
		name=player.name,
		score=score,
		opponents=frozenset(opponents),
		colors=tuple(colors),
		bye_count=bye_count,
		initial_rating=player.initial_rating,
	)


def models_q_for_player(player):
	from django.db.models import Q

	return Q(player_white=player) | Q(player_black=player) | Q(bye_player=player)
