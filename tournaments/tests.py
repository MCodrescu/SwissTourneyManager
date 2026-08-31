from django.test import TestCase

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
			PlayerCard(id=3, name='Low Prior Bye', score=0, had_bye=True),
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


class DirectorFlowTests(TestCase):
	def test_tournament_can_be_deleted_from_list(self):
		tournament = Tournament.objects.create(name='Saturday Swiss')

		response = self.client.post(f'/tournament/{tournament.id}/delete/')

		self.assertRedirects(response, '/')
		self.assertFalse(Tournament.objects.filter(id=tournament.id).exists())

	def test_overview_shows_remaining_rounds(self):
		tournament = Tournament.objects.create(name='Saturday Swiss', num_rounds=3, current_round=1)

		response = self.client.get(f'/tournament/{tournament.id}/')

		self.assertContains(response, '2 rounds remaining')

	def test_overview_labels_first_round_action_as_start_tournament(self):
		tournament = Tournament.objects.create(name='Saturday Swiss')

		response = self.client.get(f'/tournament/{tournament.id}/')

		self.assertContains(response, 'Start tournament')
		self.assertNotContains(response, 'Generate next round')

	def test_completed_tournament_can_be_closed(self):
		tournament = Tournament.objects.create(name='Saturday Swiss', num_rounds=1, current_round=1)
		Round.objects.create(tournament=tournament, round_number=1, is_completed=True)

		response = self.client.get(f'/tournament/{tournament.id}/')
		self.assertContains(response, 'Complete tournament')

		response = self.client.post(f'/tournament/{tournament.id}/complete/')
		self.assertRedirects(response, f'/tournament/{tournament.id}/standings/')
		tournament.refresh_from_db()
		self.assertFalse(tournament.is_active)

	def test_generate_round_enter_results_and_complete_round(self):
		tournament = Tournament.objects.create(name='Saturday Swiss', num_rounds=3)
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
