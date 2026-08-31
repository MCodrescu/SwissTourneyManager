from django import forms
from django.conf import settings

from .models import Pairing, Player, Tournament


class TournamentForm(forms.ModelForm):
    class Meta:
        model = Tournament
        fields = ['name', 'num_rounds']

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['num_rounds'].max_value = settings.TOURNAMENT_ROUND_LIMIT


class PlayerForm(forms.ModelForm):
    class Meta:
        model = Player
        fields = ['name', 'initial_rating']


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
