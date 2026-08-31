from django.urls import path

from . import views

app_name = 'tournaments'

urlpatterns = [
    path('', views.tournament_list, name='tournament_list'),
    path('new/', views.tournament_create, name='tournament_create'),
    path('tournament/<int:tournament_id>/delete/', views.tournament_delete, name='tournament_delete'),
    path('tournament/<int:tournament_id>/', views.tournament_detail, name='tournament_detail'),
    path('tournament/<int:tournament_id>/players/', views.player_list, name='player_list'),
    path('tournament/<int:tournament_id>/players/<int:player_id>/edit/', views.player_edit, name='player_edit'),
    path('tournament/<int:tournament_id>/players/<int:player_id>/withdraw/', views.player_withdraw, name='player_withdraw'),
    path('tournament/<int:tournament_id>/complete/', views.complete_tournament, name='complete_tournament'),
    path('tournament/<int:tournament_id>/rounds/generate/', views.generate_round, name='generate_round'),
    path('tournament/<int:tournament_id>/rounds/<int:round_id>/', views.round_detail, name='round_detail'),
    path('tournament/<int:tournament_id>/standings/', views.standings_view, name='standings'),
]
