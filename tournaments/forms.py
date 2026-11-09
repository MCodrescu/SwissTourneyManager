from django import forms
from django.conf import settings
from django.db.models import Max

from .models import Pairing, Player, Tournament


class DirectorKeyForm(forms.Form):
    director_key = forms.RegexField(
        regex=r'^[a-zA-Z0-9_-]+$',
        max_length=31,
        label='Tournament director key',
        error_messages={'invalid': 'Use only letters, numbers, hyphens, and underscores.'},
        widget=forms.TextInput(attrs={'autocomplete': 'off', 'placeholder': 'e.g. club-saturday'}),
    )

    def clean_director_key(self):
        return self.cleaned_data['director_key'].lower()


class TournamentForm(forms.ModelForm):
    class Meta:
        model = Tournament
        fields = ['name', 'num_rounds']

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['num_rounds'].max_value = settings.TOURNAMENT_ROUND_LIMIT
        if self.instance.pk and self.instance.current_round > 0:
            self.fields['num_rounds'].disabled = True


class PlayerForm(forms.ModelForm):
    class Meta:
        model = Player
        fields = ['name', 'initial_rating']
        widgets = {
            'name': forms.TextInput(attrs={'placeholder': 'Player name'}),
            'initial_rating': forms.NumberInput(attrs={'placeholder': 'Rating (optional)'}),
        }


class ExistingPlayerForm(forms.Form):
    existing_player = forms.ModelChoiceField(
        queryset=Player.objects.none(),
        label='Existing player',
        empty_label='Select a player',
    )

    def __init__(self, *args, tournament, **kwargs):
        super().__init__(*args, **kwargs)
        available_players = Player.objects.filter(
            tournament__workspace_key=tournament.workspace_key,
        ).exclude(tournament=tournament).exclude(name__in=tournament.players.values('name'))
        latest_entries = available_players.order_by().values('name').annotate(latest_id=Max('id')).values('latest_id')
        self.fields['existing_player'].queryset = available_players.filter(
            pk__in=latest_entries,
        ).select_related('tournament').order_by('name')
        self.fields['existing_player'].label_from_instance = self.player_label

    @staticmethod
    def player_label(player):
        rating = player.initial_rating if player.initial_rating is not None else 'Unrated'
        return f'{player.name} - {rating} ({player.tournament.name})'


class PairingResultForm(forms.ModelForm):
    class Meta:
        model = Pairing
        fields = ['result']

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        choices = [
            (Pairing.ResultChoices.WHITE_WIN, 'White win'),
            (Pairing.ResultChoices.BLACK_WIN, 'Black win'),
            (Pairing.ResultChoices.DRAW, 'Draw'),
        ]
        self.fields['result'].choices = choices
        self.fields['result'].widget = forms.RadioSelect(choices=choices)
