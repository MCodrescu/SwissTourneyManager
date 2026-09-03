from django.contrib.sessions.models import Session
from django.core.cache import cache
from django.core.management import call_command
from django.test import TestCase, override_settings
from django.utils import timezone

from .models import Pairing, Player, Round, Tournament
from .pairing import PlayerCard, generate_pairings, validate_no_duplicate_players
from .standings import calculate_standings


class PairingEngineTests(TestCase):
	def test_even_players_are_paired_without_duplicates(self):
		players = [PlayerCard(id=index, name=f'Player {index}') for index in range(1, 5)]

		pairings = generate_pairings(players)

		self.assertEqual(len(pairings), 2)
		self.assertTrue(validate_no_duplicate_players(pairings))
		self.assertFalse(any(pairing.is_bye for pairing in pairings))

	def test_odd_players_assign_lowest_player_without_prior_bye(self):
		players = [
			PlayerCard(id=1, name='Top', score=2),
			PlayerCard(id=2, name='Middle', score=1),
			PlayerCard(id=3, name='Low Prior Bye', score=0, bye_count=1),
			PlayerCard(id=4, name='Low', score=0),
			PlayerCard(id=5, name='Bottom', score=0),
		]

		pairings = generate_pairings(players)
		bye_ids = [pairing.bye_id for pairing in pairings if pairing.is_bye]

		self.assertEqual(bye_ids, [5])
		self.assertTrue(validate_no_duplicate_players(pairings))

	def test_avoids_rematches_when_possible(self):
		players = [
			PlayerCard(id=1, name='A', opponents=frozenset({2})),
			PlayerCard(id=2, name='B', opponents=frozenset({1})),
			PlayerCard(id=3, name='C'),
			PlayerCard(id=4, name='D'),
		]

		pairings = generate_pairings(players)
		pairs = {frozenset((pairing.white_id, pairing.black_id)) for pairing in pairings}

		self.assertNotIn(frozenset((1, 2)), pairs)

	def test_color_balance_prefers_owed_color(self):
		players = [
			PlayerCard(id=1, name='Needs Black', colors=('W', 'W')),
			PlayerCard(id=2, name='Needs White', colors=('B', 'B')),
		]

		pairings = generate_pairings(players)

		self.assertEqual(pairings[0].white_id, 2)
		self.assertEqual(pairings[0].black_id, 1)


class StandingsTests(TestCase):
	def test_scores_and_sonneborn_berger_are_computed_from_results(self):
		tournament = Tournament.objects.create(name='Club Night', num_rounds=2)
		alice = Player.objects.create(tournament=tournament, name='Alice')
		bob = Player.objects.create(tournament=tournament, name='Bob')
		cara = Player.objects.create(tournament=tournament, name='Cara')
		dan = Player.objects.create(tournament=tournament, name='Dan')
		round_one = Round.objects.create(tournament=tournament, round_number=1, is_completed=True)
		round_two = Round.objects.create(tournament=tournament, round_number=2, is_completed=True)
		Pairing.objects.create(round=round_one, player_white=alice, player_black=bob, result=Pairing.ResultChoices.WHITE_WIN)
		Pairing.objects.create(round=round_one, player_white=cara, player_black=dan, result=Pairing.ResultChoices.DRAW)
		Pairing.objects.create(round=round_two, player_white=alice, player_black=cara, result=Pairing.ResultChoices.DRAW)
		Pairing.objects.create(round=round_two, player_white=bob, player_black=dan, result=Pairing.ResultChoices.BLACK_WIN)

		rows = {row.player.name: row for row in calculate_standings(tournament)}

		self.assertEqual(rows['Alice'].score, 1.5)
		self.assertEqual(rows['Dan'].score, 1.5)
		self.assertEqual(rows['Alice'].sonneborn_berger, 0.5)
		self.assertEqual(rows['Dan'].sonneborn_berger, 0.5)
		self.assertEqual(rows['Cara'].score, 1.0)
		self.assertEqual(rows['Bob'].score, 0.0)

	def test_performance_rating_uses_1200_for_blank_opponent_rating(self):
		tournament = Tournament.objects.create(name='Club Night', num_rounds=1, is_active=False)
		alice = Player.objects.create(tournament=tournament, name='Alice', initial_rating=1400)
		bob = Player.objects.create(tournament=tournament, name='Bob')
		round_one = Round.objects.create(tournament=tournament, round_number=1, is_completed=True)
		Pairing.objects.create(
			round=round_one,
			player_white=alice,
			player_black=bob,
			result=Pairing.ResultChoices.WHITE_WIN,
		)

		rows = {row.player.name: row for row in calculate_standings(tournament)}

		self.assertEqual(rows['Alice'].performance_rating, 2000)
		self.assertEqual(rows['Bob'].performance_rating, 600)


class DirectorFlowTests(TestCase):
	def setUp(self):
		session = self.client.session
		session['workspace_initialized'] = True
		session.save()
		self.workspace_key = session.session_key

	def create_tournament(self, **kwargs):
		return Tournament.objects.create(workspace_key=self.workspace_key, **kwargs)

	def test_tournament_can_be_deleted_from_list(self):
		tournament = self.create_tournament(name='Saturday Swiss')

		response = self.client.post(f'/tournament/{tournament.id}/delete/')

		self.assertRedirects(response, '/')
		self.assertFalse(Tournament.objects.filter(id=tournament.id).exists())

	def test_new_tournament_redirects_to_overview(self):
		response = self.client.post('/new/', {'name': 'Sunday Swiss', 'num_rounds': 4})

		tournament = Tournament.objects.get(name='Sunday Swiss')
		self.assertRedirects(response, f'/tournament/{tournament.id}/')

	def test_tournament_round_count_can_be_edited_before_starting(self):
		tournament = self.create_tournament(name='Saturday Swiss', num_rounds=4)

		response = self.client.post(
			f'/tournament/{tournament.id}/edit/',
			{'name': 'Saturday Swiss', 'num_rounds': 6},
		)

		self.assertRedirects(response, f'/tournament/{tournament.id}/')
		tournament.refresh_from_db()
		self.assertEqual(tournament.num_rounds, 6)

	def test_tournament_round_count_cannot_be_edited_after_starting(self):
		tournament = self.create_tournament(name='Saturday Swiss', num_rounds=4, current_round=1)

		response = self.client.post(
			f'/tournament/{tournament.id}/edit/',
			{'name': 'Saturday Swiss', 'num_rounds': 6},
		)

		self.assertRedirects(response, f'/tournament/{tournament.id}/')
		tournament.refresh_from_db()
		self.assertEqual(tournament.num_rounds, 4)

	def test_overview_shows_completed_round_progress(self):
		tournament = self.create_tournament(name='Saturday Swiss', num_rounds=3, current_round=1)
		Round.objects.create(tournament=tournament, round_number=1, is_completed=True)

		response = self.client.get(f'/tournament/{tournament.id}/')

		self.assertContains(response, '1 of 3 rounds completed')

	def test_overview_labels_incomplete_round_as_in_progress(self):
		tournament = self.create_tournament(name='Saturday Swiss', current_round=1)
		Round.objects.create(tournament=tournament, round_number=1)

		response = self.client.get(f'/tournament/{tournament.id}/')

		self.assertContains(response, 'Round 1 - in progress')

	def test_overview_shows_exact_completed_round_duration(self):
		created_at = timezone.now() - timezone.timedelta(hours=1, minutes=2, seconds=3)
		completed_at = timezone.now()
		tournament = self.create_tournament(name='Saturday Swiss', current_round=1)
		Round.objects.create(
			tournament=tournament,
			round_number=1,
			is_completed=True,
			created_at=created_at,
			completed_at=completed_at,
		)

		response = self.client.get(f'/tournament/{tournament.id}/')

		self.assertContains(response, 'Round 1 - completed (1:02:03)')

	def test_overview_shows_completion_status_after_final_round(self):
		tournament = self.create_tournament(name='Saturday Swiss', num_rounds=1, current_round=1)
		Round.objects.create(tournament=tournament, round_number=1, is_completed=True)

		response = self.client.get(f'/tournament/{tournament.id}/')

		self.assertContains(response, '1 of 1 rounds completed')
		self.assertNotContains(response, 'Tournament Completed')

		tournament.is_active = False
		tournament.save(update_fields=['is_active'])
		response = self.client.get(f'/tournament/{tournament.id}/')

		self.assertContains(response, '<strong>Tournament Completed</strong>', html=True)

	def test_overview_labels_first_round_action_as_start_tournament(self):
		tournament = self.create_tournament(name='Saturday Swiss')

		response = self.client.get(f'/tournament/{tournament.id}/')

		self.assertContains(response, 'Start Tournament')
		self.assertContains(response, 'disabled')
		self.assertNotContains(response, 'Next Round')

	def test_overview_enables_start_with_two_active_players(self):
		tournament = self.create_tournament(name='Saturday Swiss')
		Player.objects.create(tournament=tournament, name='Alice')
		Player.objects.create(tournament=tournament, name='Bob')

		response = self.client.get(f'/tournament/{tournament.id}/')

		self.assertContains(response, 'Start Tournament')
		self.assertNotContains(response, '<button class="button primary" type="submit" disabled>', html=True)

	def test_players_page_starts_tournament_and_opens_first_round(self):
		tournament = self.create_tournament(name='Saturday Swiss')
		Player.objects.create(tournament=tournament, name='Alice')
		Player.objects.create(tournament=tournament, name='Bob')

		response = self.client.get(f'/tournament/{tournament.id}/players/')
		self.assertContains(response, 'Start Tournament')

		response = self.client.post(f'/tournament/{tournament.id}/rounds/generate/')
		round_obj = tournament.rounds.get(round_number=1)
		self.assertRedirects(
			response,
			f'/tournament/{tournament.id}/rounds/{round_obj.id}/',
			fetch_redirect_response=False,
		)

		response = self.client.get(response.url)
		self.assertContains(response, 'Tournament started. Round 1 pairings are ready.')

	def test_players_page_disables_start_with_fewer_than_two_active_players(self):
		tournament = self.create_tournament(name='Saturday Swiss')
		Player.objects.create(tournament=tournament, name='Alice')

		response = self.client.get(f'/tournament/{tournament.id}/players/')

		self.assertContains(response, '<button class="button primary" type="submit" disabled>Start Tournament</button>', html=True)

	def test_players_page_blocks_adding_players_after_tournament_starts(self):
		tournament = self.create_tournament(name='Saturday Swiss', current_round=1)
		Player.objects.create(tournament=tournament, name='Alice')
		Round.objects.create(tournament=tournament, round_number=1)

		response = self.client.post(f'/tournament/{tournament.id}/players/', {'name': 'Late Player'})

		self.assertRedirects(response, f'/tournament/{tournament.id}/players/')
		self.assertFalse(Player.objects.filter(tournament=tournament, name='Late Player').exists())

	def test_withdrawing_a_player_forfeits_their_pending_pairing(self):
		tournament = Tournament.objects.create(name='Saturday Swiss', current_round=1)
		alice = Player.objects.create(tournament=tournament, name='Alice')
		bob = Player.objects.create(tournament=tournament, name='Bob')
		round_one = Round.objects.create(tournament=tournament, round_number=1)
		pairing = Pairing.objects.create(round=round_one, player_white=alice, player_black=bob)

		alice.withdraw()

		pairing.refresh_from_db()
		self.assertEqual(pairing.result, Pairing.ResultChoices.BLACK_WIN)
		self.assertTrue(pairing.is_forfeit)

	def test_completed_tournament_can_be_closed(self):
		tournament = self.create_tournament(name='Saturday Swiss', num_rounds=1, current_round=1)
		Round.objects.create(tournament=tournament, round_number=1, is_completed=True)

		response = self.client.get(f'/tournament/{tournament.id}/')
		self.assertContains(response, 'Complete Tournament')

		response = self.client.post(f'/tournament/{tournament.id}/complete/')
		self.assertRedirects(response, f'/tournament/{tournament.id}/standings/')
		tournament.refresh_from_db()
		self.assertFalse(tournament.is_active)

	def test_generate_round_enter_results_and_complete_round(self):
		tournament = self.create_tournament(name='Saturday Swiss', num_rounds=3)
		for name in ['Alice', 'Bob', 'Cara', 'Dan']:
			Player.objects.create(tournament=tournament, name=name)

		response = self.client.post(f'/tournament/{tournament.id}/rounds/generate/')
		self.assertEqual(response.status_code, 302)

		round_obj = tournament.rounds.get(round_number=1)
		self.assertEqual(round_obj.pairings.count(), 2)
		first_pairing, second_pairing = list(round_obj.pairings.all())

		response = self.client.post(
			f'/tournament/{tournament.id}/rounds/{round_obj.id}/',
			{
				'form-TOTAL_FORMS': '2',
				'form-INITIAL_FORMS': '2',
				'form-MIN_NUM_FORMS': '0',
				'form-MAX_NUM_FORMS': '1000',
				'form-0-id': str(first_pairing.id),
				'form-0-result': Pairing.ResultChoices.WHITE_WIN,
				'form-1-id': str(second_pairing.id),
				'form-1-result': Pairing.ResultChoices.DRAW,
			},
		)
		self.assertEqual(response.status_code, 302)
		self.assertEqual(response.url, f'/tournament/{tournament.id}/')

		round_obj.refresh_from_db()
		self.assertTrue(round_obj.is_completed)

	def test_tournament_is_invisible_to_a_different_browser_session(self):
		tournament = self.create_tournament(name='Private Swiss')
		other_client = self.client_class()

		response = other_client.get('/')
		self.assertNotContains(response, tournament.name)

		response = other_client.get(f'/tournament/{tournament.id}/')
		self.assertEqual(response.status_code, 404)

	def test_start_over_clears_only_the_current_workspace(self):
		tournament = self.create_tournament(name='Private Swiss')
		other_client = self.client_class()
		other_session = other_client.session
		other_session['workspace_initialized'] = True
		other_session.save()
		other_tournament = Tournament.objects.create(
			workspace_key=other_session.session_key,
			name='Other Swiss',
		)

		response = self.client.post('/reset/')

		self.assertRedirects(response, '/')
		self.assertFalse(Tournament.objects.filter(id=tournament.id).exists())
		self.assertTrue(Tournament.objects.filter(id=other_tournament.id).exists())


class WorkspaceCleanupTests(TestCase):
	def test_cleanup_removes_expired_workspace_only(self):
		expired_session = Session.objects.create(
			session_key='expired-workspace-key',
			session_data='',
			expire_date=timezone.now() - timezone.timedelta(days=1),
		)
		active_session = Session.objects.create(
			session_key='active-workspace-key',
			session_data='',
			expire_date=timezone.now() + timezone.timedelta(days=1),
		)
		expired_tournament = Tournament.objects.create(
			workspace_key=expired_session.session_key,
			name='Expired Swiss',
		)
		active_tournament = Tournament.objects.create(
			workspace_key=active_session.session_key,
			name='Active Swiss',
		)

		call_command('purge_expired_workspaces')

		self.assertFalse(Tournament.objects.filter(id=expired_tournament.id).exists())
		self.assertTrue(Tournament.objects.filter(id=active_tournament.id).exists())
		self.assertFalse(Session.objects.filter(session_key=expired_session.session_key).exists())
		self.assertTrue(Session.objects.filter(session_key=active_session.session_key).exists())


class WorkspaceLimitTests(TestCase):
	def setUp(self):
		cache.clear()
		session = self.client.session
		session['workspace_initialized'] = True
		session.save()
		self.workspace_key = session.session_key

	@override_settings(WORKSPACE_SESSION_LIMIT=1)
	def test_session_limit_rejects_new_workspace(self):
		other_client = self.client_class()

		response = other_client.get('/', REMOTE_ADDR='192.0.2.1')

		self.assertEqual(response.status_code, 429)

	@override_settings(WORKSPACE_TOURNAMENT_LIMIT=1)
	def test_tournament_limit_rejects_new_tournament(self):
		Tournament.objects.create(workspace_key=self.workspace_key, name='Existing Swiss')

		response = self.client.post('/new/', {'name': 'One Too Many', 'num_rounds': 4})

		self.assertEqual(response.status_code, 429)
		self.assertFalse(Tournament.objects.filter(name='One Too Many').exists())

	@override_settings(TOURNAMENT_PLAYER_LIMIT=1)
	def test_player_limit_rejects_new_player(self):
		tournament = Tournament.objects.create(workspace_key=self.workspace_key, name='Private Swiss')
		Player.objects.create(tournament=tournament, name='Alice')

		response = self.client.post(f'/tournament/{tournament.id}/players/', {'name': 'Bob'})

		self.assertEqual(response.status_code, 429)
		self.assertFalse(Player.objects.filter(tournament=tournament, name='Bob').exists())

	@override_settings(WORKSPACE_REQUEST_LIMIT=1, WORKSPACE_REQUEST_WINDOW_SECONDS=60)
	def test_request_limit_rejects_excess_requests(self):
		response = self.client.get('/', REMOTE_ADDR='192.0.2.2')
		self.assertEqual(response.status_code, 200)

		response = self.client.get('/', REMOTE_ADDR='192.0.2.2')

		self.assertEqual(response.status_code, 429)
		self.assertEqual(response.headers['Retry-After'], '60')
