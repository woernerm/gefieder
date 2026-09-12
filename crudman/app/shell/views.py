from django.shortcuts import render

from .models import lately


def presence(request):
    """The list behind the bar's count: who is online and who was here this week.

    Fetched when the count is clicked rather than rendered with the bar, which stays on
    screen for hours while people come and go.
    """
    return render(request, "shell/presence.html", lately())
