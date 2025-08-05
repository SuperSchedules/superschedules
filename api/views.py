from typing import List
from datetime import date, datetime, time

from ninja import ModelSchema, Router
from django.utils import timezone

from events.models import Event


router = Router()


class EventSchema(ModelSchema):
    class Config:
        model = Event
        model_fields = [
            "id",
            "title",
            "description",
            "location",
            "start_time",
            "end_time",
            "url",
        ]


@router.get("/ping")
def ping(request):
    return {"message": f"Hello, {request.user.username}!"}


@router.get("/events", response=List[EventSchema])
def list_events(request, start: date | None = None, end: date | None = None):
    qs = Event.objects.all().order_by("start_time")

    if start or end:
        if start:
            start_dt = timezone.make_aware(datetime.combine(start, time.min))
        else:
            start_dt = timezone.now()
        qs = qs.filter(start_time__gte=start_dt)

        if end:
            end_dt = timezone.make_aware(datetime.combine(end, time.max))
            qs = qs.filter(start_time__lte=end_dt)
    else:
        qs = qs.filter(start_time__gte=timezone.now())

    return qs

