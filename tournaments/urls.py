from django.urls import path

from . import views

app_name = 'tournaments'

urlpatterns = [
    path('', views.tournament_list, name='tournament_list'),
    path('new/', views.tournament_create, name='tournament_create'),
	path('reset/', views.workspace_reset, name='workspace_reset'),
    path('tournament/<int:tournament_id>/delete/', views.tournament_delete, name='tournament_delete'),
    path('tournament/<int:tournament_id>/edit/', views.tournament_edit, name='tournament_edit'),
    path('tournament/<int:tournament_id>/', views.tournament_detail, name='tournament_detail'),
    path('tournament/<int:tournament_id>/players/', views.player_list, name='player_list'),
    path('tournament/<int:tournament_id>/players/<int:player_id>/edit/', views.player_edit, name='player_edit'),
    path('tournament/<int:tournament_id>/players/<int:player_id>/withdraw/', views.player_withdraw, name='player_withdraw'),
    path('tournament/<int:tournament_id>/complete/', views.complete_tournament, name='complete_tournament'),
    path('tournament/<int:tournament_id>/end-early/', views.end_tournament_early, name='end_tournament_early'),
    path('tournament/<int:tournament_id>/rounds/generate/', views.generate_round, name='generate_round'),
    path('tournament/<int:tournament_id>/rounds/<int:round_id>/', views.round_detail, name='round_detail'),
    path('tournament/<int:tournament_id>/rounds/<int:round_id>/start/', views.start_round, name='start_round'),
    path('tournament/<int:tournament_id>/rounds/<int:round_id>/add-player/', views.round_add_player, name='round_add_player'),
    path('tournament/<int:tournament_id>/rounds/<int:round_id>/remove/<int:player_id>/', views.pairing_remove_player, name='pairing_remove_player'),
    path('tournament/<int:tournament_id>/standings/', views.standings_view, name='standings'),
]
