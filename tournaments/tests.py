import os
import runpy
from unittest.mock import patch

from django.conf import settings
from django.contrib.sessions.models import Session
from django.core.cache import cache
from django.core.management import call_command
from django.db import connection, models
from django.db.migrations.executor import MigrationExecutor
from django.test import Client, SimpleTestCase, TestCase, TransactionTestCase, override_settings
from django.utils import timezone

from .models import Pairing, Player, Round, SavedPlayer, Tournament
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


class ModelBehaviorTests(TestCase):
	def test_pairing_scoring_and_string_helpers_cover_result_paths(self):
		tournament = Tournament.objects.create(name='Club Night', num_rounds=1)
		alice = Player.objects.create(tournament=tournament, name='Alice')
		bob = Player.objects.create(tournament=tournament, name='Bob')
		cara = Player.objects.create(tournament=tournament, name='Cara')
		round_obj = Round.objects.create(tournament=tournament, round_number=1)

		white_win = Pairing.objects.create(round=round_obj, player_white=alice, player_black=bob, result=Pairing.ResultChoices.WHITE_WIN)
		black_win = Pairing.objects.create(round=round_obj, player_white=alice, player_black=bob, result=Pairing.ResultChoices.BLACK_WIN)
		draw = Pairing.objects.create(round=round_obj, player_white=alice, player_black=bob, result=Pairing.ResultChoices.DRAW)
		bye = Pairing.objects.create(round=round_obj, bye_player=alice, result=Pairing.ResultChoices.BYE)
		pending = Pairing.objects.create(round=round_obj, player_white=alice, player_black=bob, result=Pairing.ResultChoices.PENDING)

		self.assertEqual(white_win.score_for(alice), 1.0)
		self.assertEqual(white_win.score_for(bob), 0.0)
		self.assertEqual(black_win.score_for(bob), 1.0)
		self.assertEqual(draw.score_for(alice), 0.5)
		self.assertEqual(bye.score_for(alice), 1.0)
		self.assertEqual(pending.score_for(alice), 0.0)
		self.assertEqual(white_win.opponent_for(alice), bob)
		self.assertIsNone(white_win.opponent_for(cara))
		self.assertEqual(str(white_win), 'Alice vs Bob')
		self.assertEqual(str(bye), 'Alice bye')
		self.assertIn(Pairing.ResultChoices.WHITE_WIN, Pairing.completed_results())
		self.assertIn(Pairing.ResultChoices.BYE, Pairing.completed_results())

	def test_player_withdraw_marks_status_even_without_an_active_pending_game(self):
		tournament = Tournament.objects.create(name='Club Night', num_rounds=1)
		alice = Player.objects.create(tournament=tournament, name='Alice')

		alice.withdraw()

		alice.refresh_from_db()
		self.assertTrue(alice.is_withdrawn)
		self.assertEqual(alice.withdrawn_at_round, 1)


class DatabaseConfigurationTests(SimpleTestCase):
	def test_supabase_url_uses_postgres_with_tls_and_pooler_compatible_cursors(self):
		with patch.dict(os.environ, {
			'DATABASE_URL': 'postgresql://postgres.example:password@pooler.example.com:5432/postgres',
		}):
			config = runpy.run_path(str(settings.BASE_DIR / 'chess_tournament' / 'settings.py'))
		database = config['DATABASES']['default']
		self.assertEqual(database['ENGINE'], 'django.db.backends.postgresql')
		self.assertEqual(database['HOST'], 'pooler.example.com')
		self.assertEqual(database['NAME'], 'postgres')
		self.assertEqual(database['OPTIONS']['sslmode'], 'require')
		self.assertTrue(database['DISABLE_SERVER_SIDE_CURSORS'])
		self.assertTrue(database['CONN_HEALTH_CHECKS'])

	def test_local_development_defaults_to_sqlite(self):
		with patch.dict(os.environ, {'DATABASE_URL': ''}):
			config = runpy.run_path(str(settings.BASE_DIR / 'chess_tournament' / 'settings.py'))
		self.assertEqual(config['DATABASES']['default']['ENGINE'], 'django.db.backends.sqlite3')


class DirectorKeyTests(TestCase):
	def setUp(self):
		cache.clear()

	def test_workspace_indicator_tracks_active_key_across_pages(self):
		self.assertNotContains(self.client.get('/'), 'class="workspace-indicator"')
		self.client.post('/director/', {'director_key': 'Club-Saturday'})
		self.client.post('/new/', {'name': 'Saved Swiss', 'num_rounds': 4})
		tournament = Tournament.objects.get(name='Saved Swiss')
		for path in ['/', f'/tournament/{tournament.id}/', f'/tournament/{tournament.id}/players/']:
			self.assertContains(self.client.get(path), 'Workspace: <strong>club-saturday</strong>')
		response = self.client.post('/director/', {'director_key': 'invalid key'}, follow=True)
		self.assertContains(response, 'Workspace: <strong>club-saturday</strong>')
		self.client.post('/director/leave/')
		self.client.post('/director/', {'director_key': 'other-club'})
		self.assertContains(self.client.get('/'), 'Workspace: <strong>other-club</strong>')
		self.client.post('/director/leave/')
		self.assertNotContains(self.client.get('/'), 'class="workspace-indicator"')

	def test_key_reloads_tournament_and_results_in_another_browser(self):
		self.client.post('/director/', {'director_key': 'Club-Saturday'})
		self.client.post('/new/', {'name': 'Saved Swiss', 'num_rounds': 4})
		tournament = Tournament.objects.get(name='Saved Swiss')
		alice = Player.objects.create(tournament=tournament, name='Alice')
		bob = Player.objects.create(tournament=tournament, name='Bob')
		round_obj = Round.objects.create(tournament=tournament, round_number=1, is_started=True)
		pairing = Pairing.objects.create(round=round_obj, player_white=alice, player_black=bob, result=Pairing.ResultChoices.DRAW)
		other_browser = Client()
		self.assertEqual(other_browser.get(f'/tournament/{tournament.id}/').status_code, 404)
		other_browser.post('/director/', {'director_key': 'club-saturday'})
		self.assertContains(other_browser.get('/'), 'Saved Swiss')
		self.assertEqual(other_browser.get(f'/tournament/{tournament.id}/').status_code, 200)
		self.assertEqual(tournament.workspace_key, 'director:club-saturday')
		self.assertEqual(Pairing.objects.get(pk=pairing.pk).result, Pairing.ResultChoices.DRAW)

	def test_entering_key_preserves_temporary_tournaments(self):
		self.client.post('/new/', {'name': 'Temporary Swiss', 'num_rounds': 4})
		tournament = Tournament.objects.get(name='Temporary Swiss')
		self.client.post('/director/', {'director_key': 'saved-club'})
		tournament.refresh_from_db()
		self.assertEqual(tournament.workspace_key, 'director:saved-club')
		self.assertContains(self.client.get('/'), 'Temporary Swiss')

	def test_switching_and_leaving_do_not_delete_or_move_saved_tournaments(self):
		self.client.post('/director/', {'director_key': 'first-club'})
		self.client.post('/new/', {'name': 'First Swiss', 'num_rounds': 4})
		tournament = Tournament.objects.get(name='First Swiss')
		self.client.post('/director/leave/')
		self.client.post('/director/', {'director_key': 'second-club'})
		self.assertNotContains(self.client.get('/'), 'First Swiss')
		self.assertEqual(self.client.get(f'/tournament/{tournament.id}/').status_code, 404)
		self.client.post('/director/leave/')
		self.client.post('/director/', {'director_key': 'first-club'})
		self.assertContains(self.client.get('/'), 'First Swiss')

	def test_director_key_is_locked_until_leaving_workspace(self):
		response = self.client.get('/')
		self.assertFalse(response.context['director_key_form'].fields['director_key'].disabled)
		self.client.post('/director/', {'director_key': 'first-club'})
		for _ in range(2):
			response = self.client.get('/')
			self.assertTrue(response.context['director_key_form'].fields['director_key'].disabled)
			self.assertEqual(response.context['director_key_form']['director_key'].value(), 'first-club')
			self.assertContains(response, 'type="submit" disabled>Save / Load tournaments')
		response = self.client.post('/director/', {'director_key': 'second-club'})
		self.assertRedirects(response, '/')
		self.assertEqual(self.client.session['director_workspace_key'], 'director:first-club')
		self.client.post('/director/leave/')
		response = self.client.get('/')
		self.assertFalse(response.context['director_key_form'].fields['director_key'].disabled)
		self.assertFalse(response.context['director_key_form']['director_key'].value())
		self.assertNotContains(response, 'type="submit" disabled>Save / Load tournaments')
		self.client.post('/director/', {'director_key': 'second-club'})
		self.assertEqual(self.client.session['director_workspace_key'], 'director:second-club')

	def test_invalid_keys_do_not_change_workspace(self):
		for key in ['', 'bad key', 'x' * 32, 'director:collision']:
			response = self.client.post('/director/', {'director_key': key})
			self.assertEqual(response.status_code, 200)
			self.assertTrue(response.context['director_key_form'].errors)
			self.assertNotIn('director_workspace_key', self.client.session)
		self.assertEqual(self.client.get('/director/').status_code, 405)
		self.assertEqual(self.client.get('/director/leave/').status_code, 405)

	@override_settings(WORKSPACE_TOURNAMENT_LIMIT=1)
	def test_saving_temporary_tournaments_respects_destination_limit(self):
		Tournament.objects.create(workspace_key='director:full-club', name='Existing Swiss')
		self.client.post('/new/', {'name': 'Temporary Swiss', 'num_rounds': 4})
		response = self.client.post('/director/', {'director_key': 'full-club'})
		self.assertEqual(response.status_code, 429)
		self.assertNotIn('director_workspace_key', self.client.session)
		self.assertContains(self.client.get('/'), 'Temporary Swiss')
		self.assertNotContains(self.client.get('/'), 'Existing Swiss')

	def test_saved_tournaments_survive_session_cleanup(self):
		self.client.post('/director/', {'director_key': 'lasting-club'})
		self.client.post('/new/', {'name': 'Lasting Swiss', 'num_rounds': 4})
		Session.objects.filter(session_key=self.client.session.session_key).update(
			expire_date=timezone.now() - timezone.timedelta(days=1),
		)
		call_command('purge_expired_workspaces')
		other_browser = Client()
		other_browser.post('/director/', {'director_key': 'lasting-club'})
		self.assertContains(other_browser.get('/'), 'Lasting Swiss')


class ExistingPlayerTests(TestCase):
	def setUp(self):
		cache.clear()
		self.client.post('/director/', {'director_key': 'club'})
		self.previous = Tournament.objects.create(workspace_key='director:club', name='Previous Swiss', is_active=False)
		self.tournament = Tournament.objects.create(workspace_key='director:club', name='New Swiss')
		self.player = Player.objects.create(
			tournament=self.previous, name='Alice', initial_rating=1500,
			is_withdrawn=True, withdrawn_at_round=2,
		)
		self.saved_player = SavedPlayer.objects.create(workspace_key='director:club', name='Alice', initial_rating=1500)
		self.path = f'/tournament/{self.tournament.id}/players/'

	def add_existing(self, player_id):
		return self.client.post(self.path, {'action': 'add_existing', 'existing_player': player_id})

	def test_selection_copies_name_and_rating_without_changing_previous_player(self):
		roster = self.client.get(self.path)
		self.assertContains(roster, 'Alice - 1500')
		self.assertContains(roster, '<label for="id_initial_rating">ELO Rating:</label>', html=True)
		self.assertNotContains(roster, 'Initial rating:')
		response = self.add_existing(self.saved_player.pk)
		self.assertRedirects(response, self.path)
		added = self.tournament.players.get(name='Alice')
		self.assertNotEqual(added.pk, self.player.pk)
		self.assertEqual(added.initial_rating, 1500)
		self.assertFalse(added.is_withdrawn)
		self.assertIsNone(added.withdrawn_at_round)
		self.assertFalse(added.white_pairings.exists())
		self.player.refresh_from_db()
		self.assertTrue(self.player.is_withdrawn)
		self.assertEqual(self.player.tournament_id, self.previous.pk)

	def test_dropdown_uses_directory_rating_and_excludes_current_roster(self):
		latest = Player.objects.create(tournament=self.tournament, name='Alice', initial_rating=1600)
		self.assertFalse(self.client.get(self.path).context['existing_player_form'].fields['existing_player'].queryset.exists())
		latest.tournament = Tournament.objects.create(workspace_key='director:club', name='Recent Swiss')
		latest.save()
		self.saved_player.initial_rating = 1700
		self.saved_player.save()
		options = self.client.get(self.path).context['existing_player_form'].fields['existing_player'].queryset
		self.assertEqual(list(options), [self.saved_player])
		self.assertRedirects(self.add_existing(self.saved_player.pk), self.path)
		self.assertEqual(self.tournament.players.get(name='Alice').initial_rating, 1700)
		self.assertEqual(self.add_existing(self.saved_player.pk).status_code, 200)
		self.assertEqual(self.tournament.players.count(), 1)

	def test_other_workspace_and_invalid_selections_are_rejected(self):
		private_player = SavedPlayer.objects.create(workspace_key='director:other', name='Private Player')
		self.assertNotContains(self.client.get(self.path), 'Private Player')
		for player_id in [private_player.pk, 999999, '']:
			response = self.add_existing(player_id)
			self.assertEqual(response.status_code, 200)
			self.assertTrue(response.context['existing_player_form'].errors)
		self.assertFalse(self.tournament.players.exists())

	def test_unrated_player_can_be_reused(self):
		self.saved_player.initial_rating = None
		self.saved_player.save()
		self.assertRedirects(self.add_existing(self.saved_player.pk), self.path)
		self.assertIsNone(self.tournament.players.get().initial_rating)

	def test_active_round_and_closed_tournament_block_existing_players(self):
		round_obj = Round.objects.create(tournament=self.tournament, round_number=1, is_started=True)
		self.assertContains(self.client.get(self.path), 'Players cannot be added while a round is in progress.')
		self.assertRedirects(self.add_existing(self.saved_player.pk), self.path)
		round_obj.is_completed = True
		round_obj.save()
		self.tournament.is_active = False
		self.tournament.save()
		response = self.client.get(self.path)
		self.assertContains(response, 'Players cannot be added after the tournament is over.')
		self.assertNotContains(response, 'Players cannot be added while a round is in progress.')
		self.assertRedirects(self.add_existing(self.saved_player.pk), self.path)
		self.assertFalse(self.tournament.players.exists())

	def test_ended_tournament_with_unfinished_round_uses_closed_message(self):
		Round.objects.create(tournament=self.tournament, round_number=1, is_started=True)
		self.tournament.is_active = False
		self.tournament.save()
		for payload in [
			{'name': 'New Player'},
			{'action': 'add_existing', 'existing_player': self.saved_player.pk},
		]:
			response = self.client.post(self.path, payload, follow=True)
			self.assertContains(response, 'Players cannot be added after the tournament is over.')
			self.assertNotContains(response, 'Players cannot be added while a round is in progress.')
		self.assertFalse(self.tournament.players.exists())

	@override_settings(TOURNAMENT_PLAYER_LIMIT=1)
	def test_existing_players_respect_roster_limit(self):
		Player.objects.create(tournament=self.tournament, name='Bob')
		self.assertEqual(self.add_existing(self.saved_player.pk).status_code, 429)
		self.assertEqual(self.tournament.players.count(), 1)

	def test_existing_player_is_added_to_draft_pairings(self):
		for name in ['Bob', 'Cara', 'Dan']:
			Player.objects.create(tournament=self.tournament, name=name)
		self.client.post(f'/tournament/{self.tournament.pk}/rounds/generate/')
		self.assertRedirects(self.add_existing(self.saved_player.pk), self.path)
		added = self.tournament.players.get(name='Alice')
		round_obj = self.tournament.rounds.get()
		self.assertFalse(round_obj.pairings.filter(bye_player__isnull=False).exists())
		self.assertTrue(round_obj.pairings.filter(models.Q(player_white=added) | models.Q(player_black=added)).exists())


class SavedPlayerDirectoryTests(TestCase):
	def setUp(self):
		cache.clear()
		self.client.post('/director/', {'director_key': 'club'})
		self.tournament = Tournament.objects.create(workspace_key='director:club', name='Past Swiss', is_active=False)
		self.player = Player.objects.create(
			tournament=self.tournament, name='Alice', initial_rating=1500,
			is_withdrawn=True, withdrawn_at_round=2,
		)
		self.opponent = Player.objects.create(tournament=self.tournament, name='Bob', initial_rating=1600)
		self.round = Round.objects.create(
			tournament=self.tournament, round_number=1, is_started=True, is_completed=True,
		)
		self.pairing = Pairing.objects.create(
			round=self.round, player_white=self.player, player_black=self.opponent,
			result=Pairing.ResultChoices.DRAW,
		)
		self.saved = SavedPlayer.objects.create(workspace_key='director:club', name='Alice', initial_rating=1500)
		self.list_path = '/director/players/'
		self.edit_path = f'{self.list_path}{self.saved.pk}/edit/'
		self.remove_path = f'{self.list_path}{self.saved.pk}/remove/'

	def assert_history_unchanged(self):
		self.player.refresh_from_db()
		self.pairing.refresh_from_db()
		self.assertEqual(self.player.name, 'Alice')
		self.assertEqual(self.player.initial_rating, 1500)
		self.assertTrue(self.player.is_withdrawn)
		self.assertEqual(self.player.withdrawn_at_round, 2)
		self.assertEqual(self.pairing.player_white_id, self.player.pk)
		self.assertEqual(self.pairing.player_black_id, self.opponent.pk)
		self.assertEqual(self.pairing.result, Pairing.ResultChoices.DRAW)
		self.assertEqual(self.tournament.players.count(), 2)
		self.assertEqual(calculate_standings(self.tournament)[0].score, 0.5)

	def test_directory_links_and_listing(self):
		self.assertContains(self.client.get('/'), f'href="{self.list_path}"')
		self.assertContains(self.client.get(f'/tournament/{self.tournament.pk}/players/'), 'Manage player directory')
		response = self.client.get(self.list_path)
		self.assertContains(response, 'Alice')
		self.assertContains(response, '1500')
		self.assertContains(response, self.edit_path)
		self.assertContains(response, self.remove_path)

	def test_directory_add_saves_player_for_future_reuse_without_changing_history(self):
		response = self.client.post(self.list_path, {'name': 'Cara', 'initial_rating': 1750})
		self.assertRedirects(response, self.list_path)
		saved = SavedPlayer.objects.get(workspace_key='director:club', name='Cara')
		self.assertEqual(saved.initial_rating, 1750)
		self.assert_history_unchanged()
		new_tournament = Tournament.objects.create(workspace_key='director:club', name='Future Swiss')
		path = f'/tournament/{new_tournament.pk}/players/'
		self.assertContains(self.client.get(path), 'Cara - 1750')
		self.assertRedirects(self.client.post(path, {
			'action': 'add_existing', 'existing_player': saved.pk,
		}), path)
		self.assertEqual(new_tournament.players.get().name, 'Cara')

	def test_directory_add_needs_no_tournament_and_accepts_blank_or_zero_rating(self):
		other_client = Client()
		other_client.post('/director/', {'director_key': 'empty-club'})
		for name, rating in [('Unrated Player', ''), ('Zero Player', 0)]:
			self.assertRedirects(other_client.post(self.list_path, {
				'name': name, 'initial_rating': rating,
			}), self.list_path)
		self.assertIsNone(SavedPlayer.objects.get(workspace_key='director:empty-club', name='Unrated Player').initial_rating)
		self.assertEqual(SavedPlayer.objects.get(workspace_key='director:empty-club', name='Zero Player').initial_rating, 0)
		self.assertFalse(Tournament.objects.filter(workspace_key='director:empty-club').exists())
		self.assertContains(other_client.get('/'), 'id="startOverBtn"')
		other_client.post('/reset/')
		self.assertFalse(SavedPlayer.objects.filter(workspace_key='director:empty-club').exists())

	def test_invalid_directory_add_and_duplicate_names_show_errors_without_overwriting(self):
		for payload in [
			{}, {'name': ''}, {'name': 'Alice', 'initial_rating': 1700},
			{'name': 'Bad', 'initial_rating': -1},
			{'name': 'Bad', 'initial_rating': 'bad'}, {'name': 'x' * 201},
		]:
			with self.subTest(payload=payload):
				response = self.client.post(self.list_path, payload)
				self.assertEqual(response.status_code, 200)
				self.assertTrue(response.context['form'].errors)
				self.assertContains(response, 'Alice')
		self.assertEqual(SavedPlayer.objects.filter(workspace_key='director:club').count(), 1)
		self.saved.refresh_from_db()
		self.assertEqual(self.saved.initial_rating, 1500)

	def test_directory_add_uses_current_workspace_and_allows_same_name_in_other_workspace(self):
		other_client = Client()
		other_client.post('/director/', {'director_key': 'other'})
		self.assertRedirects(other_client.post(self.list_path, {
			'name': 'Alice', 'initial_rating': 1900, 'workspace_key': 'director:club',
		}), self.list_path)
		self.assertEqual(SavedPlayer.objects.get(workspace_key='director:other', name='Alice').initial_rating, 1900)
		self.saved.refresh_from_db()
		self.assertEqual(self.saved.initial_rating, 1500)

	def test_edit_changes_future_reuse_only(self):
		response = self.client.post(self.edit_path, {'name': 'Alicia', 'initial_rating': 1750})
		self.assertRedirects(response, self.list_path)
		self.saved.refresh_from_db()
		self.assertEqual(self.saved.name, 'Alicia')
		self.assertEqual(self.saved.initial_rating, 1750)
		self.assert_history_unchanged()
		new_tournament = Tournament.objects.create(workspace_key='director:club', name='Future Swiss')
		path = f'/tournament/{new_tournament.pk}/players/'
		self.assertContains(self.client.get(path), 'Alicia - 1750')
		self.assertRedirects(self.client.post(path, {
			'action': 'add_existing', 'existing_player': self.saved.pk,
		}), path)
		added = new_tournament.players.get()
		self.assertEqual(added.name, 'Alicia')
		self.assertEqual(added.initial_rating, 1750)
		self.assertFalse(added.is_withdrawn)

	def test_remove_requires_confirmation_preserves_history_and_stays_removed(self):
		self.assertContains(self.client.get(self.remove_path), 'Remove "Alice"')
		self.assertTrue(SavedPlayer.objects.filter(pk=self.saved.pk).exists())
		self.assertRedirects(self.client.post(self.remove_path), self.list_path)
		self.assert_history_unchanged()
		for _ in range(2):
			self.assertContains(self.client.get(self.list_path), 'No saved players yet.')
		new_tournament = Tournament.objects.create(workspace_key='director:club', name='Future Swiss')
		path = f'/tournament/{new_tournament.pk}/players/'
		self.assertNotContains(self.client.get(path), 'Alice - 1500')
		response = self.client.post(path, {'action': 'add_existing', 'existing_player': self.saved.pk})
		self.assertTrue(response.context['existing_player_form'].errors)
		self.assertFalse(new_tournament.players.exists())
		self.client.post(path, {'name': 'Alice', 'initial_rating': 1650})
		self.assertEqual(SavedPlayer.objects.get(workspace_key='director:club', name='Alice').initial_rating, 1650)

	def test_invalid_and_duplicate_edits_show_errors(self):
		SavedPlayer.objects.create(workspace_key='director:club', name='Bob')
		for payload in [
			{}, {'name': ''}, {'name': 'Alice', 'initial_rating': -1},
			{'name': 'Alice', 'initial_rating': 'bad'}, {'name': 'Bob'},
		]:
			response = self.client.post(self.edit_path, payload)
			self.assertEqual(response.status_code, 200)
			self.assertTrue(response.context['form'].errors)
		self.saved.refresh_from_db()
		self.assertEqual(self.saved.name, 'Alice')
		self.assertEqual(self.saved.initial_rating, 1500)
		self.assert_history_unchanged()

	def test_unrated_and_zero_ratings(self):
		self.client.post(self.edit_path, {'name': 'Alice', 'initial_rating': ''})
		self.assertContains(self.client.get(self.list_path), 'Unrated')
		self.client.post(self.edit_path, {'name': 'Alice', 'initial_rating': 0})
		self.saved.refresh_from_db()
		self.assertEqual(self.saved.initial_rating, 0)
		self.assertContains(self.client.get(self.list_path), '<td>0</td>', html=True)

	def test_other_workspaces_cannot_list_edit_or_remove_players(self):
		other_client = Client()
		other_client.post('/director/', {'director_key': 'other'})
		SavedPlayer.objects.create(workspace_key='director:other', name='Alice')
		for client in [other_client, Client()]:
			self.assertNotContains(client.get(self.list_path), '1500')
			for path in [self.edit_path, self.remove_path]:
				self.assertEqual(client.get(path).status_code, 404)
				self.assertEqual(client.post(path, {'name': 'Changed'}).status_code, 404)
		self.assertEqual(self.client.delete(self.remove_path).status_code, 405)
		self.assertEqual(self.client.delete(self.list_path).status_code, 405)
		self.assertTrue(SavedPlayer.objects.filter(pk=self.saved.pk).exists())

	def test_directory_reloads_after_leaving_and_in_another_browser(self):
		other_client = Client()
		other_client.post('/director/', {'director_key': 'club'})
		self.assertContains(other_client.get(self.list_path), 'Alice')
		self.client.post('/director/leave/')
		self.assertNotContains(self.client.get(self.list_path), 'Alice')
		self.client.post('/director/', {'director_key': 'club'})
		self.assertContains(self.client.get(self.list_path), 'Alice')

	def test_new_and_late_entries_save_directory_and_update_rating(self):
		new_tournament = Tournament.objects.create(workspace_key='director:club', name='Future Swiss')
		path = f'/tournament/{new_tournament.pk}/players/'
		self.client.post(path, {'name': 'Alice', 'initial_rating': 1700})
		self.saved.refresh_from_db()
		self.assertEqual(self.saved.initial_rating, 1700)
		self.assertEqual(SavedPlayer.objects.filter(workspace_key='director:club', name='Alice').count(), 1)
		self.client.post(path, {'name': 'Bob'})
		self.client.post(f'/tournament/{new_tournament.pk}/rounds/generate/')
		draft = new_tournament.rounds.get()
		self.client.post(f'/tournament/{new_tournament.pk}/rounds/{draft.pk}/add-player/', {
			'name': 'Cara', 'initial_rating': 1400,
		})
		self.assertEqual(SavedPlayer.objects.get(workspace_key='director:club', name='Cara').initial_rating, 1400)
		self.assert_history_unchanged()

	def test_tournament_roster_edits_do_not_overwrite_directory(self):
		self.client.post(f'/tournament/{self.tournament.pk}/players/{self.player.pk}/edit/', {
			'name': 'Alice in history', 'initial_rating': 1800,
		})
		self.saved.refresh_from_db()
		self.assertEqual(self.saved.name, 'Alice')
		self.assertEqual(self.saved.initial_rating, 1500)

	def test_deleted_tournament_keeps_directory_and_reset_removes_it(self):
		private = SavedPlayer.objects.create(workspace_key='director:other', name='Private')
		self.client.post(f'/tournament/{self.tournament.pk}/delete/')
		self.assertContains(self.client.get(self.list_path), 'Alice')
		self.client.post('/reset/')
		self.assertFalse(SavedPlayer.objects.filter(workspace_key='director:club').exists())
		self.assertTrue(SavedPlayer.objects.filter(pk=private.pk).exists())

	def test_temporary_directory_merges_into_saved_workspace(self):
		temporary_client = Client()
		temporary_client.post('/new/', {'name': 'Temporary Swiss', 'num_rounds': 4})
		temporary_key = temporary_client.session.session_key
		tournament = Tournament.objects.get(workspace_key=temporary_key)
		temporary_client.post(f'/tournament/{tournament.pk}/players/', {'name': 'Alice', 'initial_rating': 1900})
		temporary_client.post(f'/tournament/{tournament.pk}/players/', {'name': 'Cara'})
		temporary_client.post('/director/', {'director_key': 'club'})
		self.assertFalse(SavedPlayer.objects.filter(workspace_key=temporary_key).exists())
		self.saved.refresh_from_db()
		self.assertEqual(self.saved.initial_rating, 1900)
		self.assertContains(self.client.get(self.list_path), 'Cara')
		tournament.refresh_from_db()
		self.assertEqual(tournament.workspace_key, 'director:club')


class SavedPlayerMigrationTests(TransactionTestCase):
	def test_migration_imports_latest_entries_per_workspace_without_changing_history(self):
		old_target = [('tournaments', '0006_round_start_state_and_board_numbers')]
		new_target = [('tournaments', '0007_saved_player_directory')]
		executor = MigrationExecutor(connection)
		executor.migrate(old_target)
		try:
			apps = executor.loader.project_state(old_target).apps
			HistoricalTournament = apps.get_model('tournaments', 'Tournament')
			HistoricalPlayer = apps.get_model('tournaments', 'Player')
			old = HistoricalTournament.objects.create(workspace_key='director:club', name='Old')
			recent = HistoricalTournament.objects.create(workspace_key='director:club', name='Recent')
			other = HistoricalTournament.objects.create(workspace_key='director:other', name='Other')
			first = HistoricalPlayer.objects.create(tournament=old, name='Alice', initial_rating=1500)
			HistoricalPlayer.objects.create(tournament=recent, name='Alice', initial_rating=1600)
			HistoricalPlayer.objects.create(tournament=other, name='Alice', initial_rating=1700)
			HistoricalPlayer.objects.create(tournament=recent, name='Bob')
			executor = MigrationExecutor(connection)
			executor.migrate(new_target)
			self.assertEqual(SavedPlayer.objects.count(), 3)
			self.assertEqual(SavedPlayer.objects.get(workspace_key='director:club', name='Alice').initial_rating, 1600)
			self.assertEqual(SavedPlayer.objects.get(workspace_key='director:other', name='Alice').initial_rating, 1700)
			self.assertIsNone(SavedPlayer.objects.get(name='Bob').initial_rating)
			self.assertEqual(Player.objects.get(pk=first.pk).initial_rating, 1500)
			self.assertEqual(Player.objects.count(), 4)
		finally:
			MigrationExecutor(connection).migrate(new_target)


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

	def test_invalid_tournament_create_and_edit_forms_are_rejected(self):
		invalid_create = self.client.post('/new/', {'name': '', 'num_rounds': 4})
		self.assertEqual(invalid_create.status_code, 200)
		self.assertContains(invalid_create, 'This field is required.')

		tournament = self.create_tournament(name='Saturday Swiss', num_rounds=4)
		invalid_edit = self.client.post(
			f'/tournament/{tournament.id}/edit/',
			{'name': '', 'num_rounds': 4},
		)
		self.assertEqual(invalid_edit.status_code, 200)
		self.assertContains(invalid_edit, 'This field is required.')

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
		Round.objects.create(tournament=tournament, round_number=1, is_started=True)

		response = self.client.get(f'/tournament/{tournament.id}/')

		self.assertContains(response, 'Round 1 - in progress')

	def test_overview_labels_unstarted_round_as_pairings_posted(self):
		tournament = self.create_tournament(name='Saturday Swiss')
		Round.objects.create(tournament=tournament, round_number=1)

		response = self.client.get(f'/tournament/{tournament.id}/')

		self.assertContains(response, 'Round 1 - pairings posted')
		self.assertContains(response, 'Review Round 1 Pairings')

	def test_overview_shows_exact_completed_round_duration(self):
		created_at = timezone.now() - timezone.timedelta(hours=1, minutes=2, seconds=3)
		completed_at = timezone.now()
		tournament = self.create_tournament(name='Saturday Swiss', current_round=1)
		Round.objects.create(
			tournament=tournament,
			round_number=1,
			is_completed=True,
			is_started=True,
			created_at=created_at,
			started_at=created_at,
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

	def test_overview_labels_first_round_action_as_post_pairings(self):
		tournament = self.create_tournament(name='Saturday Swiss')

		response = self.client.get(f'/tournament/{tournament.id}/')

		self.assertContains(response, 'Post Round 1 Pairings')
		self.assertContains(response, 'disabled')
		self.assertNotContains(response, 'Next Round')

	def test_overview_disables_next_round_while_a_round_is_in_progress(self):
		tournament = self.create_tournament(name='Saturday Swiss', num_rounds=3, current_round=1)
		Player.objects.create(tournament=tournament, name='Alice')
		Player.objects.create(tournament=tournament, name='Bob')
		Round.objects.create(tournament=tournament, round_number=1, is_started=True)

		response = self.client.get(f'/tournament/{tournament.id}/')

		self.assertContains(response, '<button class="button primary" type="submit" disabled>Next Round</button>', html=True)

	def test_overview_enables_next_round_once_the_round_completes(self):
		tournament = self.create_tournament(name='Saturday Swiss', num_rounds=3, current_round=1)
		Player.objects.create(tournament=tournament, name='Alice')
		Player.objects.create(tournament=tournament, name='Bob')
		Round.objects.create(tournament=tournament, round_number=1, is_started=True, is_completed=True)

		response = self.client.get(f'/tournament/{tournament.id}/')

		self.assertContains(response, '<button class="button primary" type="submit" >Next Round</button>', html=True)

	def test_overview_enables_start_with_two_active_players(self):
		tournament = self.create_tournament(name='Saturday Swiss')
		Player.objects.create(tournament=tournament, name='Alice')
		Player.objects.create(tournament=tournament, name='Bob')

		response = self.client.get(f'/tournament/{tournament.id}/')

		self.assertContains(response, 'Post Round 1 Pairings')
		self.assertNotContains(response, '<button class="button primary" type="submit" disabled>', html=True)

	def test_players_page_posts_pairings_and_opens_first_round(self):
		tournament = self.create_tournament(name='Saturday Swiss')
		Player.objects.create(tournament=tournament, name='Alice')
		Player.objects.create(tournament=tournament, name='Bob')

		response = self.client.get(f'/tournament/{tournament.id}/players/')
		self.assertContains(response, 'Post Round 1 Pairings')

		response = self.client.post(f'/tournament/{tournament.id}/rounds/generate/')
		round_obj = tournament.rounds.get(round_number=1)
		self.assertRedirects(
			response,
			f'/tournament/{tournament.id}/rounds/{round_obj.id}/',
			fetch_redirect_response=False,
		)

		# pairings exist but nothing is locked in until the round is started
		tournament.refresh_from_db()
		self.assertEqual(tournament.current_round, 0)
		self.assertFalse(round_obj.is_started)

		response = self.client.get(response.url)
		self.assertContains(response, 'Round 1 pairings posted')
		self.assertContains(response, 'Start Round')

	def test_starting_a_round_locks_it_in(self):
		tournament = self.create_tournament(name='Saturday Swiss')
		Player.objects.create(tournament=tournament, name='Alice')
		Player.objects.create(tournament=tournament, name='Bob')
		self.client.post(f'/tournament/{tournament.id}/rounds/generate/')
		round_obj = tournament.rounds.get(round_number=1)

		response = self.client.post(f'/tournament/{tournament.id}/rounds/{round_obj.id}/start/')

		self.assertRedirects(
			response,
			f'/tournament/{tournament.id}/rounds/{round_obj.id}/',
			fetch_redirect_response=False,
		)
		round_obj.refresh_from_db()
		tournament.refresh_from_db()
		self.assertTrue(round_obj.is_started)
		self.assertIsNotNone(round_obj.started_at)
		self.assertEqual(tournament.current_round, 1)
		self.assertIsNotNone(tournament.start_time)

	def test_generating_again_while_pairings_are_posted_is_rejected(self):
		tournament = self.create_tournament(name='Saturday Swiss')
		Player.objects.create(tournament=tournament, name='Alice')
		Player.objects.create(tournament=tournament, name='Bob')
		self.client.post(f'/tournament/{tournament.id}/rounds/generate/')

		self.client.post(f'/tournament/{tournament.id}/rounds/generate/')

		self.assertEqual(tournament.rounds.count(), 1)

	def test_generating_pairings_requires_at_least_two_active_players(self):
		tournament = self.create_tournament(name='Saturday Swiss')
		Player.objects.create(tournament=tournament, name='Alice')

		response = self.client.post(f'/tournament/{tournament.id}/rounds/generate/')

		self.assertRedirects(response, f'/tournament/{tournament.id}/players/')
		self.assertEqual(tournament.rounds.count(), 0)

	def test_start_round_rejects_an_already_started_or_empty_round(self):
		tournament = self.create_tournament(name='Saturday Swiss')
		for name in ['Alice', 'Bob']:
			Player.objects.create(tournament=tournament, name=name)
		self.client.post(f'/tournament/{tournament.id}/rounds/generate/')
		round_obj = tournament.rounds.get(round_number=1)
		self.client.post(f'/tournament/{tournament.id}/rounds/{round_obj.id}/start/')

		response = self.client.post(f'/tournament/{tournament.id}/rounds/{round_obj.id}/start/')
		self.assertRedirects(response, f'/tournament/{tournament.id}/rounds/{round_obj.id}/')

		empty_tournament = self.create_tournament(name='Empty Swiss')
		empty_round = Round.objects.create(tournament=empty_tournament, round_number=1)
		response = self.client.post(f'/tournament/{empty_tournament.id}/rounds/{empty_round.id}/start/')
		self.assertRedirects(response, f'/tournament/{empty_tournament.id}/rounds/{empty_round.id}/')
		self.assertFalse(empty_round.is_started)

	def test_end_tournament_early_rejects_already_ended_tournaments(self):
		tournament = self.create_tournament(name='Saturday Swiss', is_active=False)

		response = self.client.post(f'/tournament/{tournament.id}/end-early/')

		self.assertRedirects(response, f'/tournament/{tournament.id}/')
		self.assertFalse(tournament.is_active)

	def test_removing_a_player_reseats_their_opponent_without_moving_other_boards(self):
		tournament = self.create_tournament(name='Saturday Swiss')
		for index, name in enumerate(['Alice', 'Bob', 'Cara', 'Dan', 'Erin']):
			Player.objects.create(tournament=tournament, name=name, initial_rating=1500 - index * 10)
		self.client.post(f'/tournament/{tournament.id}/rounds/generate/')
		round_obj = tournament.rounds.get(round_number=1)

		board_two = round_obj.pairings.get(board_number=2)
		board_one_players = {round_obj.pairings.get(board_number=1).player_white_id, round_obj.pairings.get(board_number=1).player_black_id}
		orphan_id = board_two.player_black_id

		response = self.client.post(
			f'/tournament/{tournament.id}/rounds/{round_obj.id}/remove/{board_two.player_white_id}/'
		)

		self.assertRedirects(
			response,
			f'/tournament/{tournament.id}/rounds/{round_obj.id}/',
			fetch_redirect_response=False,
		)
		removed = Player.objects.get(id=board_two.player_white_id)
		self.assertTrue(removed.is_withdrawn)
		self.assertFalse(
			round_obj.pairings.filter(player_white=removed).exists()
			or round_obj.pairings.filter(player_black=removed).exists()
			or round_obj.pairings.filter(bye_player=removed).exists()
		)
		# board 1 is untouched
		board_one = round_obj.pairings.get(board_number=1)
		self.assertEqual({board_one.player_white_id, board_one.player_black_id}, board_one_players)
		# the orphan is still seated somewhere
		self.assertTrue(
			round_obj.pairings.filter(player_white_id=orphan_id).exists()
			or round_obj.pairings.filter(player_black_id=orphan_id).exists()
			or round_obj.pairings.filter(bye_player_id=orphan_id).exists()
		)
		# nobody is awarded a forfeit for a round that never started
		self.assertFalse(round_obj.pairings.filter(is_forfeit=True).exists())

	def test_removing_a_player_is_rejected_once_the_round_starts(self):
		tournament = self.create_tournament(name='Saturday Swiss')
		Player.objects.create(tournament=tournament, name='Alice')
		Player.objects.create(tournament=tournament, name='Bob')
		self.client.post(f'/tournament/{tournament.id}/rounds/generate/')
		round_obj = tournament.rounds.get(round_number=1)
		self.client.post(f'/tournament/{tournament.id}/rounds/{round_obj.id}/start/')
		pairing = round_obj.pairings.first()

		self.client.post(f'/tournament/{tournament.id}/rounds/{round_obj.id}/remove/{pairing.player_white_id}/')

		self.assertFalse(Player.objects.get(id=pairing.player_white_id).is_withdrawn)

	def test_players_page_disables_start_with_fewer_than_two_active_players(self):
		tournament = self.create_tournament(name='Saturday Swiss')
		Player.objects.create(tournament=tournament, name='Alice')

		response = self.client.get(f'/tournament/{tournament.id}/players/')

		self.assertContains(response, '<button class="button primary" type="submit" disabled>Post Round 1 Pairings</button>', html=True)

	def test_players_page_blocks_adding_players_while_a_round_is_in_progress(self):
		tournament = self.create_tournament(name='Saturday Swiss', current_round=1)
		Player.objects.create(tournament=tournament, name='Alice')
		Round.objects.create(tournament=tournament, round_number=1, is_started=True)

		response = self.client.post(f'/tournament/{tournament.id}/players/', {'name': 'Late Player'})

		self.assertRedirects(response, f'/tournament/{tournament.id}/players/')
		self.assertFalse(Player.objects.filter(tournament=tournament, name='Late Player').exists())

	def test_players_page_allows_adding_players_between_rounds(self):
		tournament = self.create_tournament(name='Saturday Swiss', num_rounds=3, current_round=1)
		Player.objects.create(tournament=tournament, name='Alice')
		Round.objects.create(tournament=tournament, round_number=1, is_started=True, is_completed=True)

		response = self.client.post(f'/tournament/{tournament.id}/players/', {'name': 'Late Player'})

		self.assertRedirects(response, f'/tournament/{tournament.id}/players/')
		self.assertTrue(Player.objects.filter(tournament=tournament, name='Late Player').exists())

	def test_late_player_is_slotted_into_the_open_bye_seat(self):
		tournament = self.create_tournament(name='Saturday Swiss')
		for index, name in enumerate(['Alice', 'Bob', 'Cara', 'Dan', 'Erin']):
			Player.objects.create(tournament=tournament, name=name, initial_rating=1500 - index * 10)
		self.client.post(f'/tournament/{tournament.id}/rounds/generate/')
		round_obj = tournament.rounds.get(round_number=1)
		bye_player_id = round_obj.pairings.get(bye_player__isnull=False).bye_player_id
		board_one = round_obj.pairings.get(board_number=1)
		board_one_players = {board_one.player_white_id, board_one.player_black_id}

		response = self.client.post(
			f'/tournament/{tournament.id}/rounds/{round_obj.id}/add-player/',
			{'name': 'Late Player', 'initial_rating': ''},
		)

		self.assertRedirects(
			response,
			f'/tournament/{tournament.id}/rounds/{round_obj.id}/',
			fetch_redirect_response=False,
		)
		late = Player.objects.get(tournament=tournament, name='Late Player')
		# the previously byed player comes off the bye to face the late entry
		new_board = round_obj.pairings.get(models.Q(player_white=late) | models.Q(player_black=late))
		self.assertEqual(
			{new_board.player_white_id, new_board.player_black_id},
			{late.id, bye_player_id},
		)
		self.assertFalse(round_obj.pairings.filter(bye_player__isnull=False).exists())
		self.assertEqual(
			{board_one.player_white_id, board_one.player_black_id},
			board_one_players,
		)

	def test_late_player_takes_the_bye_when_every_board_is_full(self):
		tournament = self.create_tournament(name='Saturday Swiss')
		for name in ['Alice', 'Bob', 'Cara', 'Dan']:
			Player.objects.create(tournament=tournament, name=name)
		self.client.post(f'/tournament/{tournament.id}/rounds/generate/')
		round_obj = tournament.rounds.get(round_number=1)

		self.client.post(
			f'/tournament/{tournament.id}/rounds/{round_obj.id}/add-player/',
			{'name': 'Late Player', 'initial_rating': ''},
		)

		late = Player.objects.get(tournament=tournament, name='Late Player')
		self.assertTrue(round_obj.pairings.filter(bye_player=late).exists())
		self.assertEqual(round_obj.pairings.filter(bye_player__isnull=True).count(), 2)

	def test_late_player_is_rejected_once_the_round_starts(self):
		tournament = self.create_tournament(name='Saturday Swiss')
		Player.objects.create(tournament=tournament, name='Alice')
		Player.objects.create(tournament=tournament, name='Bob')
		self.client.post(f'/tournament/{tournament.id}/rounds/generate/')
		round_obj = tournament.rounds.get(round_number=1)
		self.client.post(f'/tournament/{tournament.id}/rounds/{round_obj.id}/start/')

		self.client.post(
			f'/tournament/{tournament.id}/rounds/{round_obj.id}/add-player/',
			{'name': 'Late Player', 'initial_rating': ''},
		)

		self.assertFalse(Player.objects.filter(tournament=tournament, name='Late Player').exists())

	def test_late_player_with_a_duplicate_name_is_rejected(self):
		tournament = self.create_tournament(name='Saturday Swiss')
		for name in ['Alice', 'Bob', 'Cara', 'Dan']:
			Player.objects.create(tournament=tournament, name=name)
		self.client.post(f'/tournament/{tournament.id}/rounds/generate/')
		round_obj = tournament.rounds.get(round_number=1)

		self.client.post(
			f'/tournament/{tournament.id}/rounds/{round_obj.id}/add-player/',
			{'name': 'Alice', 'initial_rating': ''},
		)

		self.assertEqual(Player.objects.filter(tournament=tournament, name='Alice').count(), 1)

	def test_withdrawing_a_player_forfeits_their_pending_pairing(self):
		tournament = Tournament.objects.create(name='Saturday Swiss', current_round=1)
		alice = Player.objects.create(tournament=tournament, name='Alice')
		bob = Player.objects.create(tournament=tournament, name='Bob')
		round_one = Round.objects.create(tournament=tournament, round_number=1, is_started=True)
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
		self.client.post(f'/tournament/{tournament.id}/rounds/{round_obj.id}/start/')
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

	def test_performance_rating_only_counts_games_a_player_actually_played(self):
		tournament = self.create_tournament(name='Saturday Swiss', num_rounds=2, current_round=2)
		alice = Player.objects.create(tournament=tournament, name='Alice', initial_rating=1600)
		bob = Player.objects.create(tournament=tournament, name='Bob', initial_rating=1400)
		late = Player.objects.create(tournament=tournament, name='Late', initial_rating=1500)

		round_one = Round.objects.create(tournament=tournament, round_number=1, is_started=True, is_completed=True)
		Pairing.objects.create(
			round=round_one,
			player_white=alice,
			player_black=bob,
			board_number=1,
			result=Pairing.ResultChoices.WHITE_WIN,
		)
		round_two = Round.objects.create(tournament=tournament, round_number=2, is_started=True, is_completed=True)
		Pairing.objects.create(
			round=round_two,
			player_white=late,
			player_black=bob,
			board_number=1,
			result=Pairing.ResultChoices.WHITE_WIN,
		)
		Pairing.objects.create(round=round_two, bye_player=alice, result=Pairing.ResultChoices.BYE)

		rows = {row.player.name: row for row in calculate_standings(tournament)}

		# the late entry is rated on its single played game, not on the rounds it missed
		self.assertEqual(rows['Late'].games_played, 1)
		self.assertEqual(rows['Late'].performance_rating, 2200)
		# a bye is an unplayed game and is excluded from the average
		self.assertEqual(rows['Alice'].games_played, 1)
		self.assertEqual(rows['Alice'].byes, 1)
		self.assertEqual(rows['Alice'].performance_rating, 2200)
		self.assertEqual(rows['Bob'].games_played, 2)
		self.assertEqual(rows['Bob'].performance_rating, 750)

	def test_performance_rating_is_none_for_a_late_entry_who_has_only_had_a_bye(self):
		tournament = self.create_tournament(name='Saturday Swiss', num_rounds=2, current_round=1)
		alice = Player.objects.create(tournament=tournament, name='Alice', initial_rating=1600)
		bob = Player.objects.create(tournament=tournament, name='Bob', initial_rating=1400)
		late = Player.objects.create(tournament=tournament, name='Late', initial_rating=1500)
		round_one = Round.objects.create(tournament=tournament, round_number=1, is_started=True, is_completed=True)
		Pairing.objects.create(
			round=round_one,
			player_white=alice,
			player_black=bob,
			board_number=1,
			result=Pairing.ResultChoices.WHITE_WIN,
		)
		Pairing.objects.create(round=round_one, bye_player=late, result=Pairing.ResultChoices.BYE)

		rows = {row.player.name: row for row in calculate_standings(tournament)}

		self.assertEqual(rows['Late'].score, 1.0)
		self.assertEqual(rows['Late'].games_played, 0)
		self.assertIsNone(rows['Late'].performance_rating)

	def test_results_history_lists_completed_rounds_only(self):
		tournament = self.create_tournament(name='Saturday Swiss', num_rounds=3, current_round=2)
		alice = Player.objects.create(tournament=tournament, name='Alice')
		bob = Player.objects.create(tournament=tournament, name='Bob')
		round_one = Round.objects.create(tournament=tournament, round_number=1, is_started=True, is_completed=True)
		Pairing.objects.create(
			round=round_one,
			player_white=alice,
			player_black=bob,
			board_number=1,
			result=Pairing.ResultChoices.WHITE_WIN,
		)
		round_two = Round.objects.create(tournament=tournament, round_number=2, is_started=True)
		Pairing.objects.create(round=round_two, player_white=bob, player_black=alice, board_number=1)

		response = self.client.get(f'/tournament/{tournament.id}/results/')

		self.assertContains(response, 'Round 1')
		self.assertNotContains(response, 'Round 2')

	def test_results_history_can_correct_a_result_and_update_standings(self):
		tournament = self.create_tournament(name='Saturday Swiss', num_rounds=3, current_round=1)
		alice = Player.objects.create(tournament=tournament, name='Alice')
		bob = Player.objects.create(tournament=tournament, name='Bob')
		round_one = Round.objects.create(tournament=tournament, round_number=1, is_started=True, is_completed=True)
		pairing = Pairing.objects.create(
			round=round_one,
			player_white=alice,
			player_black=bob,
			board_number=1,
			result=Pairing.ResultChoices.WHITE_WIN,
			is_forfeit=True,
		)

		response = self.client.post(
			f'/tournament/{tournament.id}/results/',
			{
				'form-TOTAL_FORMS': '1',
				'form-INITIAL_FORMS': '1',
				'form-MIN_NUM_FORMS': '0',
				'form-MAX_NUM_FORMS': '1000',
				'form-0-id': str(pairing.id),
				'form-0-result': Pairing.ResultChoices.BLACK_WIN,
			},
		)

		self.assertRedirects(response, f'/tournament/{tournament.id}/results/')
		pairing.refresh_from_db()
		self.assertEqual(pairing.result, Pairing.ResultChoices.BLACK_WIN)
		self.assertFalse(pairing.is_forfeit)
		scores = {row.player.name: row.score for row in calculate_standings(tournament)}
		self.assertEqual(scores['Bob'], 1.0)
		self.assertEqual(scores['Alice'], 0.0)

	def test_results_history_is_read_only_after_the_tournament_ends(self):
		tournament = self.create_tournament(name='Saturday Swiss', num_rounds=1, current_round=1)
		alice = Player.objects.create(tournament=tournament, name='Alice')
		bob = Player.objects.create(tournament=tournament, name='Bob')
		round_one = Round.objects.create(tournament=tournament, round_number=1, is_started=True, is_completed=True)
		pairing = Pairing.objects.create(
			round=round_one,
			player_white=alice,
			player_black=bob,
			board_number=1,
			result=Pairing.ResultChoices.WHITE_WIN,
		)
		tournament.is_active = False
		tournament.save(update_fields=['is_active'])

		response = self.client.get(f'/tournament/{tournament.id}/results/')
		self.assertContains(response, 'White win')
		self.assertNotContains(response, 'Save Results')
		self.assertNotContains(response, 'type="radio"')

		response = self.client.post(
			f'/tournament/{tournament.id}/results/',
			{
				'form-TOTAL_FORMS': '1',
				'form-INITIAL_FORMS': '1',
				'form-MIN_NUM_FORMS': '0',
				'form-MAX_NUM_FORMS': '1000',
				'form-0-id': str(pairing.id),
				'form-0-result': Pairing.ResultChoices.BLACK_WIN,
			},
		)

		self.assertRedirects(response, f'/tournament/{tournament.id}/results/')
		pairing.refresh_from_db()
		self.assertEqual(pairing.result, Pairing.ResultChoices.WHITE_WIN)

	def test_results_history_is_scoped_to_the_workspace(self):
		tournament = self.create_tournament(name='Private Swiss')
		other_client = self.client_class()

		response = other_client.get(f'/tournament/{tournament.id}/results/')

		self.assertEqual(response.status_code, 404)

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
		expired_player = SavedPlayer.objects.create(workspace_key=expired_session.session_key, name='Expired')
		active_player = SavedPlayer.objects.create(workspace_key=active_session.session_key, name='Active')
		saved_player = SavedPlayer.objects.create(workspace_key='director:club', name='Saved')

		call_command('purge_expired_workspaces')

		self.assertFalse(Tournament.objects.filter(id=expired_tournament.id).exists())
		self.assertTrue(Tournament.objects.filter(id=active_tournament.id).exists())
		self.assertFalse(Session.objects.filter(session_key=expired_session.session_key).exists())
		self.assertTrue(Session.objects.filter(session_key=active_session.session_key).exists())
		self.assertFalse(SavedPlayer.objects.filter(pk=expired_player.pk).exists())
		self.assertTrue(SavedPlayer.objects.filter(pk=active_player.pk).exists())
		self.assertTrue(SavedPlayer.objects.filter(pk=saved_player.pk).exists())


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
