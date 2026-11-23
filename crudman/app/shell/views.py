from django.shortcuts import render

from .models import lately
from .stages import stages_for


def presence(request):
    """The list behind the bar's count: who is online and who was here this week.

    Fetched when the count is clicked rather than rendered with the bar, which stays on
    screen for hours while people come and go.
    """
    return render(request, "shell/presence.html", lately())


def stages(request):
    """The bar's stages, fetched again whenever a page loads in the frame.

    A right granted while the bar is up -- database access switched on, a rank changed --
    shows up on the next page rather than the next day.
    """
    return render(request, "shell/stages.html", {"stages": stages_for(request)})
