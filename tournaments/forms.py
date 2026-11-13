from django import forms
from django.conf import settings
from .models import Pairing, Player, SavedPlayer, Tournament


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
        labels = {'initial_rating': 'ELO Rating'}
        widgets = {
            'name': forms.TextInput(attrs={'placeholder': 'Player name'}),
            'initial_rating': forms.NumberInput(attrs={'placeholder': 'Rating (optional)'}),
        }


class SavedPlayerForm(PlayerForm):
    class Meta(PlayerForm.Meta):
        model = SavedPlayer

    def clean_name(self):
        name = self.cleaned_data['name']
        if SavedPlayer.objects.filter(
            workspace_key=self.instance.workspace_key,
            name=name,
        ).exclude(pk=self.instance.pk).exists():
            raise forms.ValidationError('A player with this name is already in your directory.')
        return name


class ExistingPlayerForm(forms.Form):
    existing_player = forms.ModelChoiceField(
        queryset=SavedPlayer.objects.none(),
        label='Existing player',
        empty_label='Select a player',
    )

    def __init__(self, *args, tournament, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['existing_player'].queryset = SavedPlayer.objects.filter(
            workspace_key=tournament.workspace_key,
        ).exclude(name__in=tournament.players.values('name'))
        self.fields['existing_player'].label_from_instance = self.player_label

    @staticmethod
    def player_label(player):
        rating = player.initial_rating if player.initial_rating is not None else 'Unrated'
        return f'{player.name} - {rating}'


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
