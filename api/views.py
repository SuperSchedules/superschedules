from typing import List

from ninja import ModelSchema, Router
from ninja_jwt.authentication import JWTAuth

from events.models import Event


router = Router(auth=JWTAuth())


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
def list_events(request):
    return Event.objects.all().order_by("start_time")

