from django.conf import settings
from django.contrib.sessions.models import Session
from django.core.cache import cache
from django.http import HttpResponse
from django.utils import timezone

class WorkspaceRateLimitMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if not request.session.session_key:
            active_sessions = Session.objects.filter(expire_date__gte=timezone.now()).count()
            if active_sessions >= settings.WORKSPACE_SESSION_LIMIT:
                return HttpResponse(
                    'The app has reached its temporary workspace capacity. Please try again later.',
                    status=429,
                )
            request.session.create()

        client_ip = request.META.get('REMOTE_ADDR', 'unknown')
        cache_key = f'workspace-rate:{client_ip}'
        cache.add(cache_key, 0, settings.WORKSPACE_REQUEST_WINDOW_SECONDS)
        request_count = cache.incr(cache_key)

        if request_count > settings.WORKSPACE_REQUEST_LIMIT:
            return HttpResponse(
                'Too many requests. Please wait a minute and try again.',
                status=429,
                headers={'Retry-After': str(settings.WORKSPACE_REQUEST_WINDOW_SECONDS)},
            )

        return self.get_response(request)