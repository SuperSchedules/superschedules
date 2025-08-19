from typing import List
from datetime import date, datetime, time
from urllib.parse import urlparse

from django.contrib.auth import get_user_model
from django.utils import timezone
from django.conf import settings
from django.core import signing
from django.core.mail import send_mail
from ninja import ModelSchema, Router, Schema
from ninja.errors import HttpError
from ninja_jwt.authentication import JWTAuth
from django.shortcuts import get_object_or_404
from django.core.signing import BadSignature, SignatureExpired
from uuid import uuid4

from events.models import (
    Event,
    Source,
    SiteStrategy,
    ScrapingJob,
    ScrapeBatch,
)
from api.auth import ServiceTokenAuth

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


class PasswordResetRequestSchema(Schema):
    email: str


class PasswordResetConfirmSchema(Schema):
    token: str
    password: str


class EventSchema(ModelSchema):
    class Config:
        model = Event
        model_fields = [
            "id",
            "external_id",
            "title",
            "description",
            "location",
            "start_time",
            "end_time",
            "url",
            "metadata_tags",
        ]


class EventCreateSchema(Schema):
    source_id: int | None = None
    external_id: str
    title: str
    description: str
    location: str
    start_time: datetime
    end_time: datetime | None = None
    url: str | None = None
    metadata_tags: List[str] | None = None


class EventUpdateSchema(Schema):
    source_id: int | None = None
    external_id: str | None = None
    title: str | None = None
    description: str | None = None
    location: str | None = None
    start_time: datetime | None = None
    end_time: datetime | None = None
    url: str | None = None
    metadata_tags: List[str] | None = None


class SourceSchema(ModelSchema):
    class Config:
        model = Source
        model_fields = [
            "id",
            "name",
            "base_url",
            "search_method",
            "status",
            "date_added",
            "last_run_at",
        ]


class SourceCreateSchema(Schema):
    base_url: str
    name: str | None = None
    search_method: str | None = None


class SiteStrategySchema(ModelSchema):
    class Config:
        model = SiteStrategy
        model_fields = [
            "domain",
            "best_selectors",
            "pagination_pattern",
            "cancellation_indicators",
            "success_rate",
            "total_attempts",
            "successful_attempts",
            "last_successful",
            "notes",
        ]


class SiteStrategyUpdateSchema(Schema):
    best_selectors: List[str] | None = None
    pagination_pattern: str | None = None
    cancellation_indicators: List[str] | None = None
    notes: str | None = None
    success: bool | None = None


class ScrapeRequestSchema(Schema):
    url: str


class ScrapeResultEventSchema(Schema):
    external_id: str
    title: str
    description: str
    location: str
    start_time: datetime
    end_time: datetime | None = None
    url: str | None = None
    metadata_tags: List[str] | None = None
    affiliate_link: str | None = None
    revenue_source: str | None = None
    commission_rate: float | None = None
    affiliate_tracking_id: str | None = None


class ScrapeResultSchema(Schema):
    events: List[ScrapeResultEventSchema]
    events_found: int
    pages_processed: int
    processing_time: float | None = None
    error_message: str | None = None
    success: bool = True


class ScrapingJobSchema(ModelSchema):
    class Config:
        model = ScrapingJob
        model_fields = [
            "id",
            "url",
            "domain",
            "status",
            "strategy_used",
            "events_found",
            "pages_processed",
            "processing_time",
            "error_message",
            "lambda_request_id",
            "submitted_by",
            "created_at",
            "completed_at",
        ]


class BatchRequestSchema(Schema):
    urls: List[str]


class BatchResponseSchema(Schema):
    batch_id: int
    job_ids: List[int]


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


@router.post("/reset/", auth=None)
def request_password_reset(request, payload: PasswordResetRequestSchema):
    user = User.objects.filter(email=payload.email).first()
    if user:
        token = signing.dumps({"user_id": user.id}, salt="password-reset")
        reset_link = f"{settings.FRONTEND_URL}/reset-password?token={token}"
        # Fail silently to avoid exposing mail server misconfiguration to the user
        # during the password reset request. Even if the email cannot be sent we
        # still return a generic success response for security reasons.
        try:
            send_mail(
                "Password Reset",
                f"Click the link to reset your password: {reset_link}",
                settings.DEFAULT_FROM_EMAIL,
                [user.email],
                fail_silently=True,
            )
        except Exception:
            # Intentionally ignore any e-mail errors
            pass
    return {"message": "Check your email for a password reset link."}


@router.post("/reset/confirm/", auth=None)
def confirm_password_reset(request, payload: PasswordResetConfirmSchema):
    try:
        data = signing.loads(
            payload.token,
            salt="password-reset",
            max_age=settings.PASSWORD_RESET_TIMEOUT,
        )
        user = User.objects.get(id=data["user_id"])
    except (BadSignature, SignatureExpired, User.DoesNotExist):
        raise HttpError(400, "Invalid or expired token.")

    user.set_password(payload.password)
    user.save()
    return {"message": "Password has been reset."}


@router.get("/ping", auth=JWTAuth())
def ping(request):
    return {"message": f"Hello, {request.user.username}!"}


@router.get("/sources/", auth=JWTAuth(), response=List[SourceSchema])
def list_sources(request):
    return Source.objects.filter(user=request.user)


@router.post("/sources/", auth=JWTAuth(), response={201: SourceSchema})
def create_source(request, payload: SourceCreateSchema):
    source = Source.objects.create(
        user=request.user,
        name=payload.name,
        base_url=payload.base_url,
        search_method=payload.search_method or Source.SearchMethod.MANUAL,
        status=Source.Status.NOT_RUN,
    )
    try:
        from superschedules_collector import collect_source

        collect_source(source.id)
    except Exception:
        pass
    return 201, source


@router.get(
    "/events/", auth=[JWTAuth(), ServiceTokenAuth()], response=List[EventSchema]
)
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


@router.get(
    "/events/{event_id}", auth=[JWTAuth(), ServiceTokenAuth()], response=EventSchema
)
def get_event(request, event_id: int):
    return get_object_or_404(Event, id=event_id)


@router.post("/events/", auth=ServiceTokenAuth(), response={201: EventSchema})
def create_event(request, payload: EventCreateSchema):
    if payload.source_id is not None:
        source = get_object_or_404(Source, id=payload.source_id)
    else:
        if not payload.url:
            raise HttpError(400, "Either source_id or url must be provided.")
        parsed = urlparse(payload.url)
        base_url = f"{parsed.scheme}://{parsed.netloc}"
        source, _ = Source.objects.get_or_create(base_url=base_url)

    event = Event.objects.create(
        source=source,
        external_id=payload.external_id,
        title=payload.title,
        description=payload.description,
        location=payload.location,
        start_time=payload.start_time,
        end_time=payload.end_time,
        url=payload.url,
        metadata_tags=payload.metadata_tags or [],
    )
    return 201, event


@router.put("/events/{event_id}", auth=ServiceTokenAuth(), response=EventSchema)
def update_event(request, event_id: int, payload: EventUpdateSchema):
    event = get_object_or_404(Event, id=event_id)
    data = payload.dict(exclude_unset=True)
    if "source_id" in data:
        event.source = get_object_or_404(Source, id=data.pop("source_id"))
    for attr, value in data.items():
        setattr(event, attr, value)
    event.save()
    return event


@router.delete("/events/{event_id}", auth=ServiceTokenAuth(), response={204: None})
def delete_event(request, event_id: int):
    event = get_object_or_404(Event, id=event_id)
    event.delete()
    return 204, None


@router.get("/sites/{domain}/strategy", auth=JWTAuth(), response=SiteStrategySchema)
def get_site_strategy(request, domain: str):
    strategy = get_object_or_404(SiteStrategy, domain=domain)
    return strategy


@router.post(
    "/sites/{domain}/strategy", auth=ServiceTokenAuth(), response=SiteStrategySchema
)
def report_site_strategy(request, domain: str, payload: SiteStrategyUpdateSchema):
    strategy, _ = SiteStrategy.objects.get_or_create(domain=domain)
    data = payload.dict(exclude_unset=True)
    success = data.pop("success", None)
    for attr, value in data.items():
        setattr(strategy, attr, value)
    if success is not None:
        strategy.total_attempts += 1
        if success:
            strategy.successful_attempts += 1
            strategy.last_successful = timezone.now()
        strategy.success_rate = (
            strategy.successful_attempts / strategy.total_attempts
            if strategy.total_attempts
            else 0.0
        )
    strategy.save()
    return strategy


@router.put(
    "/sites/{domain}/strategy", auth=ServiceTokenAuth(), response=SiteStrategySchema
)
def override_site_strategy(request, domain: str, payload: SiteStrategyUpdateSchema):
    strategy, _ = SiteStrategy.objects.get_or_create(domain=domain)
    data = payload.dict(exclude_unset=True)
    data.pop("success", None)
    for attr, value in data.items():
        setattr(strategy, attr, value)
    strategy.save()
    return strategy


@router.post("/scrape", auth=JWTAuth(), response=ScrapingJobSchema)
def submit_scrape(request, payload: ScrapeRequestSchema):
    parsed = urlparse(payload.url)
    domain = parsed.netloc
    strategy = SiteStrategy.objects.filter(domain=domain).first()
    job = ScrapingJob.objects.create(
        url=payload.url,
        domain=domain,
        strategy_used=",".join(strategy.best_selectors) if strategy else "",
        lambda_request_id=str(uuid4()),
        submitted_by=request.user,
    )
    return job


@router.get(
    "/scrape/{job_id}", auth=[JWTAuth(), ServiceTokenAuth()], response=ScrapingJobSchema
)
def get_scrape_job(request, job_id: int):
    return get_object_or_404(ScrapingJob, id=job_id)


@router.post("/scrape/{job_id}/results", auth=ServiceTokenAuth())
def save_scrape_results(request, job_id: int, payload: ScrapeResultSchema):
    job = get_object_or_404(ScrapingJob, id=job_id)
    parsed = urlparse(job.url)
    base_url = f"{parsed.scheme}://{parsed.netloc}"
    source_defaults = {
        "search_method": Source.SearchMethod.MANUAL,
        "user": job.submitted_by,
    }
    source, _ = Source.objects.get_or_create(base_url=base_url, defaults=source_defaults)
    created_ids = []
    for ev in payload.events:
        event = Event.objects.create(
            source=source,
            scraping_job=job,
            external_id=ev.external_id,
            title=ev.title,
            description=ev.description,
            location=ev.location,
            start_time=ev.start_time,
            end_time=ev.end_time,
            url=ev.url,
            metadata_tags=ev.metadata_tags or [],
            affiliate_link=ev.affiliate_link or "",
            revenue_source=ev.revenue_source or "",
            commission_rate=ev.commission_rate,
            affiliate_tracking_id=ev.affiliate_tracking_id or "",
        )
        created_ids.append(event.id)
    job.status = "completed" if payload.success else "failed"
    job.events_found = payload.events_found
    job.pages_processed = payload.pages_processed
    job.processing_time = payload.processing_time
    job.error_message = payload.error_message or ""
    job.completed_at = timezone.now()
    job.save()
    return {"created_event_ids": created_ids}


@router.post("/scrape/batch/", auth=JWTAuth(), response=BatchResponseSchema)
def submit_batch(request, payload: BatchRequestSchema):
    batch = ScrapeBatch.objects.create(submitted_by=request.user)
    job_ids: List[int] = []
    for url in payload.urls:
        parsed = urlparse(url)
        domain = parsed.netloc
        strategy = SiteStrategy.objects.filter(domain=domain).first()
        job = ScrapingJob.objects.create(
            url=url,
            domain=domain,
            strategy_used=",".join(strategy.best_selectors) if strategy else "",
            lambda_request_id=str(uuid4()),
            submitted_by=request.user,
        )
        batch.jobs.add(job)
        job_ids.append(job.id)
    return {"batch_id": batch.id, "job_ids": job_ids}


@router.get(
    "/scrape/batch/{batch_id}/",
    auth=[JWTAuth(), ServiceTokenAuth()],
    response=List[ScrapingJobSchema],
)
def batch_status(request, batch_id: int):
    batch = get_object_or_404(ScrapeBatch, id=batch_id)
    return list(batch.jobs.all())
