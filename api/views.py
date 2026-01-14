from typing import List
from datetime import date, datetime, time, timedelta
from urllib.parse import urlparse
import re
import requests
import logging

from asgiref.sync import async_to_sync

from django.contrib.auth import get_user_model
from django.db.models import Q
from django.utils import timezone
from django.conf import settings
from django.core import signing
from django.core.mail import send_mail
from ninja import ModelSchema, Router, Schema, Query, Field
from ninja.errors import HttpError
from ninja_jwt.authentication import JWTAuth
from django.shortcuts import get_object_or_404
from django.core.signing import BadSignature, SignatureExpired
from uuid import uuid4

from events.models import (
    Event,
    SiteStrategy,
    ScrapingJob,
    ScrapeBatch,
    ChatSession,
    ChatMessage,
)
from venues.models import Venue
from venues.extraction import normalize_venue_data, get_or_create_venue
from api.auth import ServiceTokenAuth
from api.llm_service import get_llm_service, create_event_discovery_prompt

User = get_user_model()

logger = logging.getLogger(__name__)


class UserCreateSchema(Schema):
    email: str
    password: str
    first_name: str | None = None
    last_name: str | None = None
    turnstile_token: str | None = Field(None, alias="turnstileToken")


class UserSchema(ModelSchema):
    class Meta:
        model = User
        fields = ["id", "username", "email", "first_name", "last_name"]


router = Router()


class PasswordResetRequestSchema(Schema):
    email: str


class PasswordResetConfirmSchema(Schema):
    token: str
    password: str


class EmailVerificationResendSchema(Schema):
    email: str


class MessageSchema(Schema):
    message: str


class VenueSchema(ModelSchema):
    class Meta:
        model = Venue
        fields = ["id", "name", "street_address", "city", "state", "postal_code", "latitude", "longitude"]


class VenueEnrichmentSchema(ModelSchema):
    """Venue data for enrichment API - includes enrichment fields."""
    class Meta:
        model = Venue
        fields = [
            "id", "name", "street_address", "city", "state", "postal_code",
            "venue_kind", "website_url", "description", "kids_summary",
            "enrichment_status", "last_enriched_at"
        ]


class VenueEnrichmentListSchema(Schema):
    """Response schema for needing-enrichment endpoint."""
    venues: List[VenueEnrichmentSchema]
    total_count: int


class VenueEnrichmentUpdateSchema(Schema):
    """Schema for PATCH /api/venues/{id}/ enrichment updates."""
    # Phase 1 fields
    venue_kind: str | None = None
    venue_kind_confidence: float | None = None
    venue_name_quality: str | None = None
    audience_age_groups: List[str] | None = None
    audience_tags: List[str] | None = None
    audience_min_age: int | None = None
    audience_primary: str | None = None
    # Phase 2 fields
    website_url: str | None = None
    website_url_confidence: float | None = None
    description: str | None = None
    kids_summary: str | None = None
    enrichment_status: str | None = None


class VenueFromOSMSchema(Schema):
    """Schema for POST /api/venues/from-osm/ - create/update venue from OSM data."""
    osm_type: str
    osm_id: int
    name: str
    city: str
    state: str = ""
    category: str = ""
    street_address: str = ""
    postal_code: str = ""
    latitude: float | None = None
    longitude: float | None = None
    website: str = ""
    events_url: str = ""  # Event calendar URL from Navigator
    phone: str = ""
    opening_hours: str = ""
    operator: str = ""
    wikidata: str = ""


class VenueFromOSMResponseSchema(Schema):
    """Response schema for venue from OSM endpoint."""
    venue_id: int
    status: str  # 'created', 'updated', 'unchanged'
    changes: List[str] | None = None


class VenueEventSchema(Schema):
    """Simplified event schema for venue context."""
    id: int
    title: str
    description: str
    start: datetime
    organizer: str | None = None


class VenueEventsResponseSchema(Schema):
    """Response schema for venue events endpoint."""
    events: List[VenueEventSchema]


class EventSchema(ModelSchema):
    venue: VenueSchema | None = None
    room_name: str = ""
    location: str = ""  # Computed from venue for backward compatibility

    class Meta:
        model = Event
        fields = [
            "id",
            "external_id",
            "title",
            "description",
            "start_time",
            "end_time",
            "url",
            "metadata_tags",
            "room_name",
        ]

    @staticmethod
    def resolve_venue(obj: Event) -> VenueSchema | None:
        if obj.venue:
            return VenueSchema.from_orm(obj.venue)
        return None

    @staticmethod
    def resolve_location(obj: Event) -> str:
        return obj.get_location_string()


class EventCreateSchema(Schema):
    venue_id: int | None = None  # Provide existing venue, or let location_data create one
    external_id: str
    title: str
    description: str
    location_data: dict | None = None  # Structured location for venue creation
    start_time: datetime
    end_time: datetime | None = None
    url: str | None = None
    metadata_tags: List[str] | None = None
    # Schema.org fields
    organizer: str | None = None
    event_status: str | None = None
    event_attendance_mode: str | None = None


class EventUpdateSchema(Schema):
    venue_id: int | None = None
    external_id: str | None = None
    title: str | None = None
    description: str | None = None
    start_time: datetime | None = None
    end_time: datetime | None = None
    url: str | None = None
    metadata_tags: List[str] | None = None


class SiteStrategySchema(ModelSchema):
    class Meta:
        model = SiteStrategy
        fields = [
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
    location_data: dict | None = None  # Structured location from collector
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
    class Meta:
        model = ScrapingJob
        fields = [
            "id",
            "url",
            "domain",
            "status",
            "priority",
            "strategy_used",
            "events_found",
            "pages_processed",
            "processing_time",
            "error_message",
            "lambda_request_id",
            "worker_type",
            "estimated_cost",
            "extraction_method",
            "confidence_score",
            "locked_by",
            "locked_at",
            "retry_count",
            "max_retries",
            "submitted_by",
            "created_at",
            "completed_at",
        ]


class BatchRequestSchema(Schema):
    urls: List[str]


class BatchResponseSchema(Schema):
    batch_id: int
    job_ids: List[int]


def _verify_turnstile(token: str) -> bool:
    """Verify Turnstile token with Cloudflare. Returns True if valid."""
    if not settings.TURNSTILE_SECRET_KEY:
        return True  # Skip verification if not configured

    try:
        response = requests.post(
            "https://challenges.cloudflare.com/turnstile/v0/siteverify",
            data={
                "secret": settings.TURNSTILE_SECRET_KEY,
                "response": token,
            },
            timeout=10,
        )
        result = response.json()
        success = result.get("success", False)
        if not success:
            logger.warning(f"Turnstile verification failed: {result.get('error-codes', [])}")
        return success
    except Exception as e:
        logger.error(f"Turnstile verification error: {e}")
        return False


def _send_verification_email(user):
    """Send email verification link to user. Returns True if email sent successfully."""
    token = signing.dumps({"user_id": user.id}, salt="email-verification")
    verify_link = f"{settings.FRONTEND_URL}/verify-email?token={token}"

    try:
        send_mail(
            "Verify Your EventZombie Account",
            f"Welcome to EventZombie!\n\nPlease verify your email address by clicking the link below:\n\n{verify_link}\n\nThis link will expire in 24 hours.\n\nIf you didn't create an account, you can safely ignore this email.",
            settings.DEFAULT_FROM_EMAIL,
            [user.email],
            fail_silently=False,
        )
        logger.info(f"Verification email sent to {user.email}")
        return True
    except Exception as e:
        logger.error(f"Failed to send verification email to {user.email}: {e}")
        return False


@router.post("/users", auth=None, response={201: UserSchema})
def create_user(request, payload: UserCreateSchema):
    # Verify Turnstile token if configured
    if settings.TURNSTILE_SECRET_KEY:
        if not payload.turnstile_token:
            raise HttpError(400, "Security verification required.")
        if not _verify_turnstile(payload.turnstile_token):
            raise HttpError(400, "Security verification failed. Please try again.")

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

    _send_verification_email(user)

    return 201, user


@router.post("/reset", auth=None)
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


@router.post(
    "/reset/confirm",
    auth=None,
    response={200: MessageSchema, 400: MessageSchema},
)
def confirm_password_reset(request, payload: PasswordResetConfirmSchema):
    try:
        data = signing.loads(
            payload.token,
            salt="password-reset",
            max_age=settings.PASSWORD_RESET_TIMEOUT,
        )
        user = User.objects.get(id=data["user_id"])
    except (BadSignature, SignatureExpired, User.DoesNotExist):
        # Return a JSON payload with a message key to match tests
        return 400, {"message": "Invalid or expired token."}

    user.set_password(payload.password)
    user.save()
    return {"message": "Password has been reset."}


@router.post("/users/verify/{token}", auth=None, response={200: MessageSchema, 400: MessageSchema})
def verify_email(request, token: str):
    """Verify user's email address using the token from the verification email."""
    try:
        data = signing.loads(
            token,
            salt="email-verification",
            max_age=settings.EMAIL_VERIFICATION_TIMEOUT,
        )
        user = User.objects.get(id=data["user_id"])
    except SignatureExpired:
        return 400, {"message": "Verification link has expired. Please request a new one."}
    except (BadSignature, User.DoesNotExist):
        return 400, {"message": "Invalid verification link."}

    if user.is_active:
        return 200, {"message": "Email already verified. You can log in."}

    user.is_active = True
    user.save()
    logger.info(f"User {user.email} verified their email address")
    return 200, {"message": "Email verified successfully. You can now log in."}


@router.post("/users/resend-verification", auth=None, response={200: MessageSchema})
def resend_verification_email(request, payload: EmailVerificationResendSchema):
    """Resend verification email to user."""
    user = User.objects.filter(email=payload.email, is_active=False).first()
    if user:
        _send_verification_email(user)
    # Always return success to prevent email enumeration
    return {"message": "If an unverified account exists with this email, a verification link has been sent."}


@router.get("/ping", auth=JWTAuth())
def ping(request):
    return {"message": f"Hello, {request.user.username}!"}


# NOTE: Source endpoints (/sources) have been removed - venues are now the first-class citizen
# Use /venues/from-osm/ to create venues with events_urls


@router.get(
    "/events", auth=[ServiceTokenAuth(), JWTAuth()], response=List[EventSchema]
)
def list_events(
    request,
    start: date | None = None,
    end: date | None = None,
    ids: List[int] = Query(None),
    location_id: int | None = Query(None, description="Filter by location ID (from /locations/suggest)"),
    radius_miles: float = Query(10.0, description="Search radius in miles (default 10, used with location_id)"),
):
    from locations.models import Location
    from locations.services import filter_by_distance

    qs = Event.objects.all().order_by("start_time")

    # If specific IDs are requested, filter by those and ignore date filters
    if ids is not None and len(ids) > 0:
        qs = qs.filter(id__in=ids)
        return qs

    # Apply location-based filtering if location_id provided
    if location_id is not None:
        try:
            location = Location.objects.get(id=location_id)
            qs = filter_by_distance(
                qs,
                lat=float(location.latitude),
                lng=float(location.longitude),
                radius_miles=radius_miles,
            )
        except Location.DoesNotExist:
            pass  # Invalid location_id, ignore silently

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
    "/events/{event_id}", auth=[ServiceTokenAuth(), JWTAuth()], response=EventSchema
)
def get_event(request, event_id: int):
    return get_object_or_404(Event, id=event_id)


@router.post("/events", auth=ServiceTokenAuth(), response={201: EventSchema})
def create_event(request, payload: EventCreateSchema):
    """Create event - venue-first architecture."""
    # Determine venue from venue_id or location_data
    venue = None
    if payload.venue_id is not None:
        venue = get_object_or_404(Venue, id=payload.venue_id)

    try:
        event, _ = Event.create_with_schema_org_data(
            {
                'external_id': payload.external_id,
                'title': payload.title,
                'description': payload.description,
                'location_data': payload.location_data,
                'start_time': payload.start_time,
                'end_time': payload.end_time,
                'url': payload.url,
                'tags': payload.metadata_tags or [],
                'organizer': payload.organizer or '',
                'event_status': payload.event_status or '',
                'event_attendance_mode': payload.event_attendance_mode or '',
            },
            venue=venue,
            source_url=payload.url or '',
        )
        return 201, event
    except ValueError as e:
        raise HttpError(400, str(e))


@router.put("/events/{event_id}", auth=ServiceTokenAuth(), response=EventSchema)
def update_event(request, event_id: int, payload: EventUpdateSchema):
    event = get_object_or_404(Event, id=event_id)
    data = payload.dict(exclude_unset=True)
    if "venue_id" in data:
        event.venue = get_object_or_404(Venue, id=data.pop("venue_id"))
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


# Allow authenticated users (JWT) to override strategies via PUT
@router.put(
    "/sites/{domain}/strategy", auth=JWTAuth(), response=SiteStrategySchema
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
    """Submit URL for asynchronous processing (frontend endpoint)."""
    parsed = urlparse(payload.url)
    domain = parsed.netloc

    # Check if there's already a pending or processing job for this URL
    existing_job = ScrapingJob.objects.filter(
        url=payload.url,
        status__in=['pending', 'processing']
    ).first()

    if existing_job:
        logger.info(f"URL {payload.url} already queued (job {existing_job.id})")
        return existing_job

    # Check if URL was recently processed successfully (within 14 days)
    from datetime import timedelta
    recent_success = ScrapingJob.objects.filter(
        url=payload.url,
        status='completed',
        completed_at__gte=timezone.now() - timedelta(days=14)
    ).first()

    if recent_success:
        logger.info(f"URL {payload.url} recently processed (job {recent_success.id})")
        return recent_success

    # Find venue that has this URL in events_urls
    venue = Venue.objects.filter(events_urls__contains=[payload.url]).first()

    # Create new job for queue (linked to venue if found)
    job = ScrapingJob.objects.create(
        url=payload.url,
        domain=domain,
        status='pending',
        submitted_by=request.user,
        venue=venue,
        priority=5,  # Normal priority for manual submissions
        lambda_request_id=str(uuid4()),
    )

    logger.info(f"Job {job.id} queued for {payload.url}")
    return job


# Batch endpoints MUST be defined before /scrape/{job_id} to avoid route conflicts
@router.post("/scrape/batch", auth=JWTAuth(), response=BatchResponseSchema)
def submit_batch(request, payload: BatchRequestSchema):
    """Submit multiple URLs for batch processing."""
    batch = ScrapeBatch.objects.create(submitted_by=request.user)
    job_ids: List[int] = []

    for url in payload.urls:
        parsed = urlparse(url)
        domain = parsed.netloc

        # Check for existing pending/processing job
        existing_job = ScrapingJob.objects.filter(
            url=url,
            status__in=['pending', 'processing']
        ).first()

        if existing_job:
            batch.jobs.add(existing_job)
            job_ids.append(existing_job.id)
            continue

        # Find venue that has this URL in events_urls
        venue = Venue.objects.filter(events_urls__contains=[url]).first()

        # Create new job with lower priority for batch
        job = ScrapingJob.objects.create(
            url=url,
            domain=domain,
            status='pending',
            submitted_by=request.user,
            venue=venue,
            priority=7,  # Lower priority for batch submissions
            lambda_request_id=str(uuid4()),
        )
        batch.jobs.add(job)
        job_ids.append(job.id)

    logger.info(f"Batch {batch.id}: {len(job_ids)} jobs queued")
    return {"batch_id": batch.id, "job_ids": job_ids}


@router.get(
    "/scrape/batch/{batch_id}",
    auth=[ServiceTokenAuth(), JWTAuth()],
    response=List[ScrapingJobSchema],
)
def batch_status(request, batch_id: int):
    """Get status of all jobs in a batch."""
    batch = get_object_or_404(ScrapeBatch, id=batch_id)
    return list(batch.jobs.all())


@router.get(
    "/scrape/{job_id}", auth=[ServiceTokenAuth(), JWTAuth()], response=ScrapingJobSchema
)
def get_scrape_job(request, job_id: int):
    return get_object_or_404(ScrapingJob, id=job_id)


@router.post("/scrape/{job_id}/results", auth=ServiceTokenAuth())
def save_scrape_results(request, job_id: int, payload: ScrapeResultSchema):
    """Save scraping results - venue-first architecture."""
    job = get_object_or_404(ScrapingJob, id=job_id)
    parsed = urlparse(job.url)
    source_domain = parsed.netloc

    created_ids = []
    updated_ids = []
    skipped_count = 0

    for ev in payload.events:
        # Venue is required - create from location_data
        venue = None
        room_name = ""
        if ev.location_data:
            normalized = normalize_venue_data(location_data=ev.location_data)
            if normalized.get('venue_name') and normalized.get('city'):
                venue, _ = get_or_create_venue(normalized, source_domain)
                room_name = (normalized.get('room_name') or '')[:200]

        if not venue:
            logger.warning(f"Skipping event '{ev.title}': no venue could be determined from location_data")
            skipped_count += 1
            continue

        # Link job to venue if not already linked
        if job.venue is None:
            job.venue = venue

        # Venue-first deduplication: (venue, external_id)
        event, was_created = Event.objects.update_or_create(
            venue=venue,
            external_id=ev.external_id,
            defaults={
                "scraping_job": job,
                "title": ev.title,
                "description": ev.description,
                "room_name": room_name,
                "start_time": ev.start_time,
                "end_time": ev.end_time,
                "url": ev.url,
                "metadata_tags": ev.metadata_tags or [],
                "affiliate_link": ev.affiliate_link or "",
                "revenue_source": ev.revenue_source or "",
                "commission_rate": ev.commission_rate,
                "affiliate_tracking_id": ev.affiliate_tracking_id or "",
            }
        )
        if was_created:
            created_ids.append(event.id)
        else:
            updated_ids.append(event.id)

    job.status = "completed" if payload.success else "failed"
    job.events_found = payload.events_found
    job.pages_processed = payload.pages_processed
    job.processing_time = payload.processing_time
    job.error_message = payload.error_message or ""
    job.completed_at = timezone.now()
    job.save()

    if skipped_count > 0:
        logger.warning(f"Job {job_id}: skipped {skipped_count} events without venue data")

    return {"created_event_ids": created_ids, "updated_event_ids": updated_ids}


# Job Queue Management Endpoints


@router.post("/queue/submit", auth=JWTAuth(), response=ScrapingJobSchema)
def submit_url_to_queue(request, payload: ScrapeRequestSchema):
    """Submit URL for asynchronous processing."""
    parsed = urlparse(payload.url)
    domain = parsed.netloc

    # Check if there's already a pending or processing job for this URL
    existing_job = ScrapingJob.objects.filter(
        url=payload.url,
        status__in=['pending', 'processing']
    ).first()

    if existing_job:
        logger.info(f"URL {payload.url} already queued (job {existing_job.id})")
        return existing_job

    # Check if URL was recently processed successfully (within 14 days)
    from datetime import timedelta
    recent_success = ScrapingJob.objects.filter(
        url=payload.url,
        status='completed',
        completed_at__gte=timezone.now() - timedelta(days=14)
    ).first()

    if recent_success:
        logger.info(f"URL {payload.url} recently processed (job {recent_success.id})")
        return recent_success

    # Find venue that has this URL in events_urls
    venue = Venue.objects.filter(events_urls__contains=[payload.url]).first()

    # Create new job (linked to venue if found)
    job = ScrapingJob.objects.create(
        url=payload.url,
        domain=domain,
        status='pending',
        submitted_by=request.user,
        venue=venue,
        priority=5
    )

    logger.info(f"Job {job.id} queued for {payload.url}")
    return job


@router.get("/queue/next", auth=ServiceTokenAuth(), response=ScrapingJobSchema)
def get_next_job(request, worker_id: str = Query(...)):
    """Workers call this to get next job (atomic claim with SELECT FOR UPDATE)."""
    from django.db import transaction

    with transaction.atomic():
        job = ScrapingJob.objects.select_for_update(skip_locked=True).filter(
            status='pending'
        ).order_by('priority', 'created_at').first()

        if not job:
            raise HttpError(404, "No pending jobs available")

        job.status = 'processing'
        job.locked_at = timezone.now()
        job.locked_by = worker_id
        job.save()

        logger.info(f"Job {job.id} claimed by worker {worker_id}")
        return job


@router.post("/queue/{job_id}/complete", auth=ServiceTokenAuth())
def complete_job(request, job_id: int, payload: ScrapeResultSchema):
    """Worker reports job completion with events - venue-first architecture."""
    job = get_object_or_404(ScrapingJob, id=job_id)

    created_ids = []
    updated_ids = []
    skipped_count = 0

    for event_data in payload.events:
        try:
            event, was_created = Event.create_with_schema_org_data(
                {
                    'external_id': event_data.external_id,
                    'title': event_data.title,
                    'description': event_data.description,
                    'location_data': event_data.location_data,
                    'start_time': event_data.start_time,
                    'end_time': event_data.end_time,
                    'url': event_data.url,
                    'tags': event_data.metadata_tags or [],
                },
                source_url=job.url,
                venue=job.venue,  # Use venue linked to job if available
            )
            # Link job to venue from first event if not already set
            if job.venue is None and event.venue:
                job.venue = event.venue

            if was_created:
                created_ids.append(event.id)
            else:
                updated_ids.append(event.id)
        except ValueError as e:
            logger.warning(f"Skipping event '{event_data.title}': {e}")
            skipped_count += 1

    job.status = 'completed' if payload.success else 'failed'
    job.events_found = len(created_ids) + len(updated_ids)
    job.processing_time = payload.processing_time
    job.error_message = payload.error_message or ''
    job.completed_at = timezone.now()
    job.save()

    if skipped_count > 0:
        logger.warning(f"Job {job_id}: skipped {skipped_count} events without venue data")

    logger.info(f"Job {job_id} completed: {len(created_ids)} created, {len(updated_ids)} updated")
    return {"created_event_ids": created_ids, "updated_event_ids": updated_ids}


@router.get("/queue/status", auth=JWTAuth())
def queue_status(request):
    """Get queue statistics."""
    from django.db.models import Count, Q

    stats = ScrapingJob.objects.aggregate(
        pending=Count('id', filter=Q(status='pending')),
        processing=Count('id', filter=Q(status='processing')),
        completed_today=Count('id', filter=Q(
            status='completed',
            completed_at__gte=timezone.now() - timedelta(days=1)
        )),
        failed_today=Count('id', filter=Q(
            status='failed',
            completed_at__gte=timezone.now() - timedelta(days=1)
        ))
    )

    return {
        "queue_depth": stats['pending'],
        "processing": stats['processing'],
        "completed_24h": stats['completed_today'],
        "failed_24h": stats['failed_today']
    }


@router.post("/queue/bulk-submit", auth=JWTAuth())
def bulk_submit_urls(request, payload: BatchRequestSchema):
    """Submit multiple URLs for processing (daily re-scrape use case)."""
    jobs = []
    for url in payload.urls:
        parsed = urlparse(url)

        # Check for existing pending/processing job
        existing_job = ScrapingJob.objects.filter(
            url=url,
            status__in=['pending', 'processing']
        ).first()

        if existing_job:
            jobs.append(existing_job)
            continue

        # Find venue that has this URL in events_urls
        venue = Venue.objects.filter(events_urls__contains=[url]).first()

        # Create new job with lower priority for bulk
        job = ScrapingJob.objects.create(
            url=url,
            domain=parsed.netloc,
            status='pending',
            submitted_by=request.user,
            venue=venue,
            priority=7  # Lower priority for bulk
        )
        jobs.append(job)

    logger.info(f"Bulk submit: {len(jobs)} jobs queued")
    return {"submitted": len(jobs), "job_ids": [j.id for j in jobs]}


@router.post("/queue/bulk-submit-service", auth=ServiceTokenAuth())
def bulk_submit_urls_service(request, payload: BatchRequestSchema):
    """
    Bulk submit URLs using service token (for administrative bulk loading).
    Uses first superuser as the submitter since service tokens don't have users.
    """
    admin_user = User.objects.filter(is_superuser=True).first()

    if not admin_user:
        raise HttpError(500, "No admin user found")

    jobs = []
    skipped = 0

    for url in payload.urls:
        parsed = urlparse(url)

        # Check for existing pending/processing job
        existing_job = ScrapingJob.objects.filter(
            url=url,
            status__in=['pending', 'processing']
        ).first()

        if existing_job:
            jobs.append(existing_job)
            skipped += 1
            continue

        # Find venue that has this URL in events_urls
        venue = Venue.objects.filter(events_urls__contains=[url]).first()

        # Create new job with lower priority for bulk
        job = ScrapingJob.objects.create(
            url=url,
            domain=parsed.netloc,
            status='pending',
            submitted_by=admin_user,
            venue=venue,
            priority=7  # Lower priority for bulk
        )
        jobs.append(job)

    logger.info(f"Service bulk submit: {len(jobs)} jobs total ({len(jobs)-skipped} new, {skipped} existing)")
    return {
        "submitted": len(jobs),
        "new_jobs": len(jobs) - skipped,
        "existing_jobs": skipped,
        "job_ids": [j.id for j in jobs]
    }


# Chat API Schemas and Endpoints

# A/B testing endpoint removed - functionality moved to streaming chat service

# Simple context class for message parsing utilities
class ChatContextSchema:
    def __init__(self, location=None):
        self.location = location


# Removed A/B testing chat response function - functionality moved to streaming service


def _get_relevant_event_ids(ages: List[int] | None, location: str | None, timeframe: str, user) -> List[int]:
    """
    Stub function to get relevant event IDs.
    In production, this will use pgvector for similarity search.
    """
    
    # Get some events from the database as a stub
    qs = Event.objects.all()
    
    # Apply basic filtering (in production, this would be much more sophisticated)
    if location:
        qs = qs.filter(Q(venue__city__icontains=location) | Q(venue__name__icontains=location))
    
    if timeframe == 'today':
        start_date = timezone.now().date()
        end_date = start_date
    elif timeframe == 'tomorrow':
        start_date = timezone.now().date() + timedelta(days=1)
        end_date = start_date
    elif 'week' in timeframe:
        start_date = timezone.now().date()
        end_date = start_date + timedelta(days=7)
    else:
        start_date = timezone.now().date()
        end_date = start_date + timedelta(days=30)
    
    start_dt = timezone.make_aware(datetime.combine(start_date, time.min))
    end_dt = timezone.make_aware(datetime.combine(end_date, time.max))
    qs = qs.filter(start_time__gte=start_dt, start_time__lte=end_dt)
    
    # Return up to 3 event IDs
    return list(qs.values_list('id', flat=True)[:3])


def _parse_ages_from_message(message: str) -> List[int] | None:
    """Extract age ranges from message."""
    age_match = re.search(r'(\d+)[\s-]*(?:and|to|-)?\s*(\d+)?\s*year[s]?\s*old', message.lower())
    if age_match:
        ages = [int(age_match.group(1))]
        if age_match.group(2):
            ages.append(int(age_match.group(2)))
        return ages
    return None


def _parse_location_from_message(message: str, context: ChatContextSchema) -> str | None:
    """Extract location from message or context."""
    location_match = re.search(r'(?:in|at|near)\s+([a-zA-Z\s,]+?)(?:\s*[^\w\s]|\s*$)', message.lower())
    if location_match:
        return location_match.group(1).strip(' ,')
    return context.location


def _parse_timeframe_from_message(message: str) -> str:
    """Extract timeframe from message."""
    time_match = re.search(r'(today|tomorrow|this\s+(?:weekend|week|month)|next\s+(?:\d+\s+)?(?:hours?|days?|week|month))', message.lower())
    return time_match.group(1) if time_match else 'upcoming'


def _detect_topic_change(message: str) -> bool:
    """Detect if message indicates a topic change."""
    topic_shift_keywords = ['actually', 'instead', 'nevermind', 'different', 'change']
    return any(keyword in message.lower() for keyword in topic_shift_keywords)


def _extract_follow_up_questions(response: str) -> List[str]:
    """Extract follow-up questions from LLM response."""
    # Simple heuristic - look for sentences ending with ?
    import re
    questions = re.findall(r'[^.!?]*\?', response)
    return [q.strip() for q in questions[:3]]  # Limit to 3 questions


# =============================================================================
# Chat Session API - Conversation Memory
# =============================================================================

class ChatMessageSchema(Schema):
    id: int
    role: str
    content: str
    created_at: datetime
    metadata: dict = {}


class ChatSessionSchema(Schema):
    id: int
    title: str
    created_at: datetime
    updated_at: datetime
    is_active: bool
    context: dict = {}
    message_count: int = 0

    @staticmethod
    def resolve_message_count(obj):
        return obj.messages.count()


class ChatSessionDetailSchema(Schema):
    id: int
    title: str
    created_at: datetime
    updated_at: datetime
    is_active: bool
    context: dict = {}
    messages: List[ChatMessageSchema] = []

    @staticmethod
    def resolve_messages(obj):
        return list(obj.messages.order_by('created_at')[:50])


class CreateSessionSchema(Schema):
    title: str = ""
    context: dict = {}


class UpdateSessionSchema(Schema):
    title: str | None = None
    context: dict | None = None


class AddMessageSchema(Schema):
    role: str  # 'user' or 'assistant'
    content: str
    metadata: dict = {}
    event_ids: List[int] = []


@router.get("/chat/sessions", auth=JWTAuth(), response=List[ChatSessionSchema])
def list_chat_sessions(request, active_only: bool = True, limit: int = 20):
    """List user's chat sessions, most recent first."""
    qs = ChatSession.objects.filter(user=request.user)
    if active_only:
        qs = qs.filter(is_active=True)
    return list(qs.order_by('-updated_at')[:limit])


@router.post("/chat/sessions", auth=JWTAuth(), response={201: ChatSessionSchema})
def create_chat_session(request, payload: CreateSessionSchema):
    """Create a new chat session."""
    session = ChatSession.objects.create(
        user=request.user,
        title=payload.title or "",
        context=payload.context or {}
    )
    return 201, session


@router.get("/chat/sessions/{session_id}", auth=JWTAuth(), response=ChatSessionDetailSchema)
def get_chat_session(request, session_id: int):
    """Get session with messages."""
    session = get_object_or_404(ChatSession, id=session_id, user=request.user)
    return session


@router.put("/chat/sessions/{session_id}", auth=JWTAuth(), response=ChatSessionSchema)
def update_chat_session(request, session_id: int, payload: UpdateSessionSchema):
    """Update session title or context."""
    session = get_object_or_404(ChatSession, id=session_id, user=request.user)
    if payload.title is not None:
        session.title = payload.title
    if payload.context is not None:
        session.context = payload.context
    session.save()
    return session


@router.post("/chat/sessions/{session_id}/archive", auth=JWTAuth())
def archive_chat_session(request, session_id: int):
    """Archive (soft-delete) a session."""
    session = get_object_or_404(ChatSession, id=session_id, user=request.user)
    session.is_active = False
    session.save()
    return {"status": "archived", "session_id": session_id}


@router.delete("/chat/sessions/{session_id}", auth=JWTAuth(), response={204: None})
def delete_chat_session(request, session_id: int):
    """Permanently delete a session and all its messages."""
    session = get_object_or_404(ChatSession, id=session_id, user=request.user)
    session.delete()
    return 204, None


@router.get("/chat/sessions/{session_id}/messages", auth=JWTAuth(), response=List[ChatMessageSchema])
def get_session_messages(request, session_id: int, limit: int = 50, offset: int = 0):
    """Get messages for a session with pagination."""
    session = get_object_or_404(ChatSession, id=session_id, user=request.user)
    messages = session.messages.order_by('created_at')[offset:offset + limit]
    return list(messages)


@router.post("/chat/sessions/{session_id}/messages", auth=JWTAuth(), response={201: ChatMessageSchema})
def add_session_message(request, session_id: int, payload: AddMessageSchema):
    """Add a message to a session (used by chat service or for manual additions)."""
    session = get_object_or_404(ChatSession, id=session_id, user=request.user)

    # Validate role
    if payload.role not in ['user', 'assistant', 'system']:
        raise HttpError(400, "Invalid role. Must be 'user', 'assistant', or 'system'")

    message = ChatMessage.objects.create(
        session=session,
        role=payload.role,
        content=payload.content,
        metadata=payload.metadata or {}
    )

    # Link referenced events
    if payload.event_ids:
        events = Event.objects.filter(id__in=payload.event_ids)
        message.referenced_events.set(events)

    # Auto-generate title from first user message
    if payload.role == 'user' and not session.title:
        session.title = payload.content[:50] + ("..." if len(payload.content) > 50 else "")
        session.save(update_fields=['title', 'updated_at'])

    return 201, message


@router.get("/chat/sessions/{session_id}/history", auth=JWTAuth())
def get_session_history_for_llm(request, session_id: int, limit: int = 10):
    """Get recent messages formatted for LLM context."""
    session = get_object_or_404(ChatSession, id=session_id, user=request.user)
    messages = session.get_recent_messages(limit=limit)
    return {
        "session_id": session_id,
        "messages": [{"role": msg.role, "content": msg.content} for msg in messages]
    }


# =============================================================================
# Venue Enrichment API - For Collector Service
# =============================================================================

@router.get("/venues", auth=ServiceTokenAuth(), response=VenueEnrichmentListSchema)
def list_venues(
    request,
    limit: int = Query(500, description="Max venues to return"),
):
    """
    Returns all venues for enrichment processing.
    Used by collector service fallback when Phase 2 endpoint returns empty.
    """
    qs = Venue.objects.all().order_by('-created_at')
    total_count = qs.count()
    venues = list(qs[:limit])
    return {"venues": venues, "total_count": total_count}


@router.get("/venues/needing-enrichment", auth=ServiceTokenAuth(), response=VenueEnrichmentListSchema)
def get_venues_needing_enrichment(
    request,
    limit: int = Query(50, description="Max venues to return"),
    missing: str | None = Query(None, description="Filter by missing field: website_url, description, kids_summary"),
):
    """
    Returns venues that need Phase 2 enrichment.
    Prioritizes venues with venue_kind set (Phase 1 complete) and more events.
    """
    from django.db.models import Count

    # Base query: venues with Phase 1 complete (has venue_kind)
    qs = Venue.objects.exclude(venue_kind__isnull=True).exclude(venue_kind='').exclude(venue_kind='unknown')

    # Filter by specific missing field or any missing enrichment data
    if missing == 'website_url':
        qs = qs.filter(Q(website_url__isnull=True) | Q(website_url=''))
    elif missing == 'description':
        qs = qs.filter(description='')
    elif missing == 'kids_summary':
        qs = qs.filter(kids_summary='')
    else:
        # Default: any missing enrichment field
        qs = qs.filter(
            Q(website_url__isnull=True) | Q(website_url='') |
            Q(description='') |
            Q(kids_summary='')
        )

    # Annotate with event count and order by most events first (richer context)
    qs = qs.annotate(event_count=Count('events')).order_by('-event_count', '-created_at')

    total_count = qs.count()
    venues = list(qs[:limit])

    return {"venues": venues, "total_count": total_count}


@router.get("/venues/{venue_id}/events", auth=ServiceTokenAuth(), response=VenueEventsResponseSchema)
def get_venue_events(
    request,
    venue_id: int,
    limit: int = Query(20, description="Max events to return"),
    future_only: bool = Query(False, description="Only include future events"),
):
    """
    Returns recent events at a specific venue for LLM context during enrichment.
    Returns mix of recent past events and upcoming events.
    """
    venue = get_object_or_404(Venue, id=venue_id)

    qs = Event.objects.filter(venue=venue)

    if future_only:
        qs = qs.filter(start_time__gte=timezone.now())
    else:
        # Get mix of recent past and future events
        thirty_days_ago = timezone.now() - timedelta(days=30)
        qs = qs.filter(start_time__gte=thirty_days_ago)

    # Order by date descending (most recent first)
    qs = qs.order_by('-start_time')[:limit]

    events = [
        {
            "id": e.id,
            "title": e.title,
            "description": e.description,
            "start": e.start_time,
            "organizer": e.organizer or None,
        }
        for e in qs
    ]

    return {"events": events}


@router.patch("/venues/{venue_id}", auth=ServiceTokenAuth(), response=VenueEnrichmentSchema)
def update_venue_enrichment(request, venue_id: int, payload: VenueEnrichmentUpdateSchema):
    """
    Updates venue with enrichment results from collector service.
    Only updates provided fields (partial update).
    Explicitly provided None values will clear the field (for nullable fields).
    """
    venue = get_object_or_404(Venue, id=venue_id)

    update_fields = []
    data = payload.dict(exclude_unset=True)

    # Fields that can be explicitly set to None/null
    nullable_fields = {'audience_min_age', 'website_url', 'website_url_confidence', 'kids_summary'}

    for field, value in data.items():
        if value is not None or field in nullable_fields:
            setattr(venue, field, value)
            update_fields.append(field)

    # Always update last_enriched_at if any enrichment data was provided
    if update_fields:
        venue.last_enriched_at = timezone.now()
        update_fields.append('last_enriched_at')
        venue.save(update_fields=update_fields)
        logger.info(f"Venue {venue_id} enriched: updated {update_fields}")

    return venue


@router.post("/venues/from-osm/", auth=ServiceTokenAuth(), response={201: VenueFromOSMResponseSchema, 200: VenueFromOSMResponseSchema, 400: dict})
def create_or_update_venue_from_osm(request, payload: VenueFromOSMSchema):
    """
    Create or update a Venue from OpenStreetMap data.

    Idempotent - uses osm_type + osm_id for lookup. Returns:
    - 201 with status='created' for new venues
    - 200 with status='updated' and changes list for modified venues
    - 200 with status='unchanged' for identical data
    """
    from django.utils.text import slugify

    # Validate required fields
    if not payload.name or not payload.city:
        raise HttpError(400, "name and city are required fields")

    # Try to find existing venue by OSM ID
    try:
        venue = Venue.objects.get(osm_type=payload.osm_type, osm_id=payload.osm_id)
        return _update_osm_venue(venue, payload)
    except Venue.DoesNotExist:
        return _create_osm_venue(payload)


def _create_osm_venue(payload: VenueFromOSMSchema):
    """Create a new venue from OSM data."""
    from django.utils.text import slugify

    # Build events_urls list if provided
    events_urls = [payload.events_url] if payload.events_url else []

    venue = Venue.objects.create(
        name=payload.name,
        slug=slugify(payload.name),
        osm_type=payload.osm_type,
        osm_id=payload.osm_id,
        category=payload.category,
        street_address=payload.street_address,
        city=payload.city,
        state=payload.state,
        postal_code=payload.postal_code,
        latitude=payload.latitude,
        longitude=payload.longitude,
        canonical_url=payload.website,
        events_urls=events_urls,
        phone=payload.phone,
        opening_hours_raw=payload.opening_hours,
        operator=payload.operator,
        wikidata_id=payload.wikidata,
        data_source='osm',
    )

    logger.info(f"Created venue {venue.id} from OSM: {payload.osm_type}/{payload.osm_id} ({venue.name})")

    return 201, {"venue_id": venue.id, "status": "created"}


def _update_osm_venue(venue: Venue, payload: VenueFromOSMSchema):
    """Update an existing venue from OSM data, tracking changes."""
    changes = []

    # Map of payload field -> model field
    updatable_fields = [
        ('name', 'name'),
        ('street_address', 'street_address'),
        ('city', 'city'),
        ('state', 'state'),
        ('postal_code', 'postal_code'),
        ('latitude', 'latitude'),
        ('longitude', 'longitude'),
        ('website', 'canonical_url'),
        ('phone', 'phone'),
        ('opening_hours', 'opening_hours_raw'),
        ('operator', 'operator'),
        ('wikidata', 'wikidata_id'),
        ('category', 'category'),
    ]

    for payload_field, model_field in updatable_fields:
        new_value = getattr(payload, payload_field)
        old_value = getattr(venue, model_field)

        # Handle decimal comparison for lat/lng
        if model_field in ('latitude', 'longitude'):
            if new_value is not None and old_value is not None:
                if abs(float(new_value) - float(old_value)) > 0.000001:
                    setattr(venue, model_field, new_value)
                    changes.append(payload_field)
            elif new_value != old_value:
                setattr(venue, model_field, new_value)
                changes.append(payload_field)
        elif new_value != old_value:
            setattr(venue, model_field, new_value)
            changes.append(payload_field)

    # Handle events_url - add to list if not already present
    if payload.events_url:
        current_urls = venue.events_urls or []
        if payload.events_url not in current_urls:
            venue.events_urls = current_urls + [payload.events_url]
            changes.append('events_url')

    if changes:
        venue.save()
        logger.info(f"Updated venue {venue.id} from OSM: changed {changes}")
        return 200, {"venue_id": venue.id, "status": "updated", "changes": changes}
    else:
        return 200, {"venue_id": venue.id, "status": "unchanged"}
