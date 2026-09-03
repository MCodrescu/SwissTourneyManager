from django.core.validators import MinValueValidator
from django.db import models, transaction
from django.utils import timezone


class Tournament(models.Model):
	workspace_key = models.CharField(max_length=40, db_index=True)
	name = models.CharField(max_length=200)
	num_rounds = models.PositiveIntegerField(default=4, validators=[MinValueValidator(1)])
	current_round = models.PositiveIntegerField(default=0)
	created_at = models.DateTimeField(auto_now_add=True)
	is_active = models.BooleanField(default=True)
	start_time = models.DateTimeField(blank=True, null=True)
	end_time = models.DateTimeField(blank=True, null=True)

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

	def pending_pairing(self):
		return Pairing.objects.filter(
			models.Q(player_white=self) | models.Q(player_black=self),
			round__tournament=self.tournament,
			result=Pairing.ResultChoices.PENDING,
		).select_related('round').order_by('-round__round_number').first()

	def withdraw(self):
		with transaction.atomic():
			pending = self.pending_pairing()
			if pending is not None:
				# award the opponent a win since the withdrawn player can no longer finish this game
				pending.result = Pairing.ResultChoices.BLACK_WIN if pending.player_white_id == self.id else Pairing.ResultChoices.WHITE_WIN
				pending.is_forfeit = True
				pending.save(update_fields=['result', 'is_forfeit'])

			self.is_withdrawn = True
			self.withdrawn_at_round = self.tournament.current_round + 1
			self.save(update_fields=['is_withdrawn', 'withdrawn_at_round'])


class Round(models.Model):
	tournament = models.ForeignKey(Tournament, on_delete=models.CASCADE, related_name='rounds')
	round_number = models.PositiveIntegerField()
	is_completed = models.BooleanField(default=False)
	created_at = models.DateTimeField(default=timezone.now)
	completed_at = models.DateTimeField(blank=True, null=True)

	class Meta:
		ordering = ['round_number']
		unique_together = [('tournament', 'round_number')]

	def __str__(self):
		return f'{self.tournament} - Round {self.round_number}'

	@property
	def elapsed_time(self):
		if self.completed_at is None:
			return None
		total_seconds = max(0, int((self.completed_at - self.created_at).total_seconds()))
		hours, remainder = divmod(total_seconds, 3600)
		minutes, seconds = divmod(remainder, 60)
		return f'{hours}:{minutes:02d}:{seconds:02d}'


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
	is_forfeit = models.BooleanField(default=False)

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
