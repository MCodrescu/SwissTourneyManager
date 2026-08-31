from django.db import models
from django.utils import timezone


class Tournament(models.Model):
	name = models.CharField(max_length=200)
	num_rounds = models.PositiveIntegerField(default=4)
	current_round = models.PositiveIntegerField(default=0)
	created_at = models.DateTimeField(auto_now_add=True)
	is_active = models.BooleanField(default=True)

	class Meta:
		ordering = ['-created_at']

	def __str__(self):
		return self.name


class Player(models.Model):
	tournament = models.ForeignKey(Tournament, on_delete=models.CASCADE, related_name='players')
	name = models.CharField(max_length=200)
	initial_rating = models.PositiveIntegerField(blank=True, null=True)
	is_withdrawn = models.BooleanField(default=False)
	withdrawn_at_round = models.PositiveIntegerField(blank=True, null=True)

	class Meta:
		ordering = ['name']
		unique_together = [('tournament', 'name')]

	def __str__(self):
		return self.name

	def withdraw(self):
		self.is_withdrawn = True
		self.withdrawn_at_round = self.tournament.current_round + 1
		self.save(update_fields=['is_withdrawn', 'withdrawn_at_round'])

	def get_score(self):
		score = 0.0
		for pairing in self.pairings():
			if pairing.result == Pairing.ResultChoices.BYE and pairing.bye_player_id == self.id:
				score += 1.0
			elif pairing.result == Pairing.ResultChoices.WHITE_WIN and pairing.player_white_id == self.id:
				score += 1.0
			elif pairing.result == Pairing.ResultChoices.BLACK_WIN and pairing.player_black_id == self.id:
				score += 1.0
			elif pairing.result == Pairing.ResultChoices.DRAW:
				score += 0.5
		return score

	def get_opponents(self):
		opponents = []
		for pairing in self.pairings().select_related('player_white', 'player_black'):
			opponent = pairing.opponent_for(self)
			if opponent is not None:
				opponents.append(opponent)
		return opponents

	def get_colors_played(self):
		colors = []
		for pairing in self.pairings():
			if pairing.player_white_id == self.id:
				colors.append('W')
			elif pairing.player_black_id == self.id:
				colors.append('B')
		return colors

	def pairings(self):
		return Pairing.objects.filter(
			models.Q(player_white=self) | models.Q(player_black=self) | models.Q(bye_player=self),
			result__in=Pairing.completed_results(),
		).select_related('round').order_by('round__round_number')


class Round(models.Model):
	tournament = models.ForeignKey(Tournament, on_delete=models.CASCADE, related_name='rounds')
	round_number = models.PositiveIntegerField()
	is_completed = models.BooleanField(default=False)
	created_at = models.DateTimeField(default=timezone.now)

	class Meta:
		ordering = ['round_number']
		unique_together = [('tournament', 'round_number')]

	def __str__(self):
		return f'{self.tournament} - Round {self.round_number}'


class Pairing(models.Model):
	class ResultChoices(models.TextChoices):
		WHITE_WIN = 'WHITE_WIN', 'White win'
		BLACK_WIN = 'BLACK_WIN', 'Black win'
		DRAW = 'DRAW', 'Draw'
		BYE = 'BYE', 'Bye'
		PENDING = 'PENDING', 'Pending'

	round = models.ForeignKey(Round, on_delete=models.CASCADE, related_name='pairings')
	player_white = models.ForeignKey(Player, on_delete=models.CASCADE, related_name='white_pairings', blank=True, null=True)
	player_black = models.ForeignKey(Player, on_delete=models.CASCADE, related_name='black_pairings', blank=True, null=True)
	bye_player = models.ForeignKey(Player, on_delete=models.CASCADE, related_name='bye_pairings', blank=True, null=True)
	result = models.CharField(max_length=20, choices=ResultChoices.choices, default=ResultChoices.PENDING)

	class Meta:
		ordering = ['round__round_number', 'id']

	def __str__(self):
		if self.bye_player_id:
			return f'{self.bye_player} bye'
		return f'{self.player_white} vs {self.player_black}'

	@classmethod
	def completed_results(cls):
		return [
			cls.ResultChoices.WHITE_WIN,
			cls.ResultChoices.BLACK_WIN,
			cls.ResultChoices.DRAW,
			cls.ResultChoices.BYE,
		]

	def opponent_for(self, player):
		if self.player_white_id == player.id:
			return self.player_black
		if self.player_black_id == player.id:
			return self.player_white
		return None

	def score_for(self, player):
		if self.result == self.ResultChoices.BYE and self.bye_player_id == player.id:
			return 1.0
		if self.result == self.ResultChoices.WHITE_WIN:
			return 1.0 if self.player_white_id == player.id else 0.0
		if self.result == self.ResultChoices.BLACK_WIN:
			return 1.0 if self.player_black_id == player.id else 0.0
		if self.result == self.ResultChoices.DRAW and player.id in {self.player_white_id, self.player_black_id}:
			return 0.5
		return 0.0
