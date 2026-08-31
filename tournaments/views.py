from django.contrib import messages
from django.db import IntegrityError, transaction
from django.forms import modelformset_factory
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
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
		tournament = form.save(commit=False)
		tournament.workspace_key = _workspace_key(request)
		tournament.save()
		return redirect(PLAYER_LIST_ROUTE, tournament_id=tournament.id)
	return render(request, 'tournaments/form.html', {'form': form, 'title': 'New tournament'})


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
	rounds_remaining = max(tournament.num_rounds - tournament.current_round, 0)
	completed_round_count = rounds.filter(is_completed=True).count()
	can_complete_tournament = (
		tournament.is_active
		and tournament.current_round == tournament.num_rounds
		and rounds.count() == tournament.num_rounds
		and not rounds.filter(is_completed=False).exists()
	)
	return render(request, 'tournaments/tournament_detail.html', {
		'tournament': tournament,
		'rounds': rounds,
		'standings': standings,
		'rounds_remaining': rounds_remaining,
		'completed_round_count': completed_round_count,
		'can_complete_tournament': can_complete_tournament,
	})


def player_list(request, tournament_id):
	tournament = _workspace_tournament(request, tournament_id)
	players = tournament.players.all()
	active_player_count = players.filter(is_withdrawn=False).count()
	tournament_started = tournament.current_round > 0
	form = PlayerForm(request.POST or None)
	if request.method == 'POST':
		if tournament_started:
			messages.error(request, 'Players can only be added before the tournament starts.')
			return redirect(PLAYER_LIST_ROUTE, tournament_id=tournament.id)
		if form.is_valid():
			player = form.save(commit=False)
			player.tournament = tournament
			player.save()
			messages.success(request, f'Added {player.name}.')
			return redirect(PLAYER_LIST_ROUTE, tournament_id=tournament.id)
	return render(request, 'tournaments/player_list.html', {
		'tournament': tournament,
		'players': players,
		'active_player_count': active_player_count,
		'tournament_started': tournament_started,
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
	player.withdraw()
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
		tournament.save(update_fields=['is_active'])
		messages.success(request, f'{tournament.name} completed.')
		return redirect('tournaments:standings', tournament_id=tournament.id)
	else:
		messages.error(request, 'Complete all scheduled rounds before completing the tournament.')
	return redirect('tournaments:tournament_detail', tournament_id=tournament.id)


@require_POST
def generate_round(request, tournament_id):
	tournament = _workspace_tournament(request, tournament_id)
	if tournament.current_round >= tournament.num_rounds:
		messages.error(request, 'This tournament has already reached its scheduled round count.')
		return redirect('tournaments:tournament_detail', tournament_id=tournament.id)

	latest_round = tournament.rounds.order_by('-round_number').first()
	if latest_round and not latest_round.is_completed:
		messages.error(request, 'Complete the current round before generating another one.')
		return redirect(ROUND_DETAIL_ROUTE, tournament_id=tournament.id, round_id=latest_round.id)

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
			latest_round = tournament.rounds.order_by('-round_number').first()
			if latest_round and not latest_round.is_completed:
				messages.error(request, 'Complete the current round before generating another one.')
				return redirect(ROUND_DETAIL_ROUTE, tournament_id=tournament.id, round_id=latest_round.id)

			round_number = tournament.current_round + 1
			round_obj = Round.objects.create(tournament=tournament, round_number=round_number)
			player_cards = [_player_card(player, tournament) for player in active_players]
			cards = generate_pairings(player_cards)
			players_by_id = {player.id: player for player in active_players}

			for card in cards:
				if card.is_bye:
					Pairing.objects.create(round=round_obj, bye_player=players_by_id[card.bye_id], result=Pairing.ResultChoices.BYE)
				else:
					Pairing.objects.create(
						round=round_obj,
						player_white=players_by_id[card.white_id],
						player_black=players_by_id[card.black_id],
					)

			tournament.current_round = round_number
			tournament.save(update_fields=['current_round'])
	except IntegrityError:
		messages.error(request, 'Another request already generated this round. Please refresh and try again.')
		return redirect('tournaments:tournament_detail', tournament_id=tournament.id)

	messages.success(request, 'Tournament started. Round 1 pairings are ready.')
	return redirect(ROUND_DETAIL_ROUTE, tournament_id=tournament.id, round_id=round_obj.id)


def round_detail(request, tournament_id, round_id):
	tournament = _workspace_tournament(request, tournament_id)
	round_obj = get_object_or_404(Round.objects.prefetch_related('pairings'), id=round_id, tournament=tournament)
	editable_pairings = round_obj.pairings.exclude(result=Pairing.ResultChoices.BYE)
	result_formset = modelformset_factory(Pairing, form=PairingResultForm, extra=0)
	formset = result_formset(request.POST or None, queryset=editable_pairings)

	if request.method == 'POST' and not round_obj.is_completed and formset.is_valid():
		with transaction.atomic():
			formset.save()
			if not round_obj.pairings.filter(result=Pairing.ResultChoices.PENDING).exists():
				round_obj.is_completed = True
				round_obj.save(update_fields=['is_completed'])
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
