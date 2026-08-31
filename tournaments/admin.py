from django.contrib import admin

from .models import Pairing, Player, Round, Tournament


@admin.register(Tournament)
class TournamentAdmin(admin.ModelAdmin):
	list_display = ('name', 'num_rounds', 'current_round', 'is_active', 'created_at')
	search_fields = ('name',)


@admin.register(Player)
class PlayerAdmin(admin.ModelAdmin):
	list_display = ('name', 'tournament', 'initial_rating', 'is_withdrawn', 'withdrawn_at_round')
	list_filter = ('tournament', 'is_withdrawn')
	search_fields = ('name',)


@admin.register(Round)
class RoundAdmin(admin.ModelAdmin):
	list_display = ('tournament', 'round_number', 'is_completed', 'created_at')
	list_filter = ('tournament', 'is_completed')


@admin.register(Pairing)
class PairingAdmin(admin.ModelAdmin):
	list_display = ('round', 'player_white', 'player_black', 'bye_player', 'result')
	list_filter = ('round__tournament', 'result')
