from typing import List
from datetime import date, datetime, time

from django.contrib.auth import get_user_model
from django.utils import timezone
from ninja import ModelSchema, Router, Schema
from ninja.errors import HttpError

from events.models import Event

User = get_user_model()


class UserCreateSchema(Schema):
    email: str
    password: str
    first_name: str | None = None
    last_name: str | None = None


class UserSchema(ModelSchema):
    class Config:
        model = User
        model_fields = ["id", "username", "email", "first_name", "last_name"]


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


@router.post("/users/", auth=None, response={201: UserSchema})
def create_user(request, payload: UserCreateSchema):
    if User.objects.filter(username=payload.email).exists():
        raise HttpError(400, "A user with this email already exists.")

    user = User.objects.create_user(
        username=payload.email,
        email=payload.email,
        password=payload.password,
        first_name=payload.first_name or "",
        last_name=payload.last_name or "",
        is_active=False,
    )

    return 201, user


@router.get("/ping")
def ping(request):
    return {"message": f"Hello, {request.user.username}!"}


@router.get("/events/", response=List[EventSchema])
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

