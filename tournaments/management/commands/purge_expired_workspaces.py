from django.contrib.sessions.models import Session
from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils import timezone

from tournaments.models import Tournament


class Command(BaseCommand):
    help = 'Remove expired browser-session workspaces and their tournament data.'

    def handle(self, *args, **options):
        with transaction.atomic():
            expired_sessions = Session.objects.filter(expire_date__lt=timezone.now())
            workspace_keys = list(expired_sessions.values_list('session_key', flat=True))
            deleted_tournaments, _ = Tournament.objects.filter(
                workspace_key__in=workspace_keys,
            ).delete()
            deleted_sessions, _ = expired_sessions.delete()

        self.stdout.write(
            self.style.SUCCESS(
                f'Removed {deleted_tournaments} expired tournaments and '
                f'{deleted_sessions} expired sessions.'
            )
        )