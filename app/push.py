"""Web Push notifications (see PushSubscription in app/models.py).

`send_push` delivers to a single subscription via pywebpush, signing with the VAPID key
pair from environment variables (VAPID_PUBLIC_KEY/VAPID_PRIVATE_KEY/VAPID_CLAIMS_EMAIL -
see config.py and scripts/generate_vapid_keys.py). A subscription the push service reports
as gone (404/410 - the browser revoked it, or it expired) is deleted so future sends stop
retrying it; any other failure (network blip, misconfigured keys, push service outage) is
logged and swallowed - a failed notification should never break the caller (a scheduler
tick, an admin publishing a gameweek, ...).
"""

import json
import logging
from urllib.parse import urlparse

from flask import current_app
from pywebpush import WebPushException, webpush

from app.extensions import db
from app.models import GameweekEntry, Notification, PushSubscription

logger = logging.getLogger(__name__)


def _endpoint_host(subscription):
    return urlparse(subscription.endpoint).netloc or "unknown"


def _send(subscription, title, body, url, notification_id):
    """Send to one subscription and log the outcome. Returns (ok, reason); reason is None on success."""
    sub_id = subscription.id
    host = _endpoint_host(subscription)

    vapid_private_key = current_app.config.get("VAPID_PRIVATE_KEY")
    vapid_claims_email = current_app.config.get("VAPID_CLAIMS_EMAIL")
    if not vapid_private_key or not vapid_claims_email:
        reason = "VAPID keys not configured"
        logger.warning("Push not sent subscription=%s host=%s: %s", sub_id, host, reason)
        return False, reason

    try:
        response = webpush(
            subscription_info={
                "endpoint": subscription.endpoint,
                "keys": {"p256dh": subscription.p256dh, "auth": subscription.auth},
            },
            data=json.dumps({"title": title, "body": body, "url": url, "notification_id": notification_id}),
            vapid_private_key=vapid_private_key,
            vapid_claims={"sub": vapid_claims_email},
        )
        logger.info(
            "Push sent subscription=%s host=%s status=%s", sub_id, host, getattr(response, "status_code", "unknown")
        )
        return True, None
    except WebPushException as exc:
        response = exc.response
        status_code = response.status_code if response is not None else None
        text = (response.text or "")[:300] if response is not None else ""
        reason = f"{type(exc).__name__} status={status_code} response={text!r}"
        logger.warning("Push send failed subscription=%s host=%s %s", sub_id, host, reason)
        if status_code in (404, 410):
            db.session.delete(subscription)
            db.session.commit()
            logger.warning("Push subscription=%s host=%s removed as expired (status %s)", sub_id, host, status_code)
        return False, reason
    except Exception as exc:  # noqa: BLE001 - a bad send must never take down the caller
        reason = f"{type(exc).__name__} status=None response={str(exc)[:300]!r}"
        logger.warning("Push send failed subscription=%s host=%s %s", sub_id, host, reason)
        return False, reason


def send_push(subscription, title, body, url="/", notification_id=None):
    """Send to one subscription. Returns True if delivered, False otherwise (VAPID not
    configured, the subscription is gone, or any other failure - all logged, never raised).
    """
    return _send(subscription, title, body, url, notification_id)[0]


def log_vapid_status(app):
    """Log at startup which VAPID settings are present (true/false only, never the values)."""
    public = bool(app.config.get("VAPID_PUBLIC_KEY"))
    private = bool(app.config.get("VAPID_PRIVATE_KEY"))
    subject = app.config.get("VAPID_CLAIMS_EMAIL") or ""
    logger.info(
        "VAPID configuration: public_key=%s private_key=%s subject=%s",
        public, private, bool(subject),
    )
    missing = [
        name
        for name, present in (("VAPID_PUBLIC_KEY", public), ("VAPID_PRIVATE_KEY", private), ("VAPID_CLAIMS_EMAIL", bool(subject)))
        if not present
    ]
    if missing:
        logger.warning("Push notifications will not be delivered: %s not set", ", ".join(missing))
    if subject and not subject.startswith(("mailto:", "https://")):
        logger.warning("VAPID_CLAIMS_EMAIL must start with mailto: or https:// - sends will fail until it does")


SHORT_BODY_LIMIT = 100


def make_short_body(body):
    """Key point first, under 100 characters: the first sentence if it fits, else a word-boundary cut."""
    body = " ".join(body.split())
    if len(body) < SHORT_BODY_LIMIT:
        return body
    first_sentence = body.split(". ")[0].rstrip(".")
    if len(first_sentence) < SHORT_BODY_LIMIT and first_sentence != body:
        return first_sentence + "."
    cut = body[: SHORT_BODY_LIMIT - 1].rsplit(" ", 1)[0].rstrip(",;:- ")
    return cut + "\u2026"


def _deliver(user_ids, title, body, url, short_body=None, report=None):
    """Create one inbox row per recipient, then push each of their subscriptions.

    The push carries the short text and points at /notifications/<id>; the full text and
    the original `url` live on the Notification row. Returns (sent, total) subscriptions.
    """
    short = short_body or make_short_body(body)
    sent = total = 0
    for user_id in sorted(user_ids):
        notification = Notification(user_id=user_id, title=title, short_body=short, full_body=body, url=url)
        db.session.add(notification)
        db.session.commit()
        subscriptions = PushSubscription.query.filter_by(user_id=user_id).all()
        total += len(subscriptions)
        for subscription in subscriptions:
            ok, reason = _send(subscription, title, short, f"/notifications/{notification.id}", notification.id)
            if ok:
                sent += 1
            elif report is not None:
                report["failures"].append(reason)
    return sent, total


def notify_all(title, body, url="/", report=None):
    """Returns (sent, total) so callers can report delivery diagnostics if they want to.

    Pass `report={"failures": []}` to collect each failed send's reason.
    """
    user_ids = {user_id for (user_id,) in db.session.query(PushSubscription.user_id).distinct()}
    return _deliver(user_ids, title, body, url, report=report)


def notify_gameweek_participants(gameweek, title, body, url="/"):
    """Send to users opted in (GameweekEntry.opted_in=True) to `gameweek`'s pot.

    Returns (sent, total) so callers can report delivery diagnostics if they want to.
    """
    user_ids = {
        entry.user_id
        for entry in GameweekEntry.query.filter_by(gameweek_id=gameweek.id, opted_in=True)
    }
    if not user_ids:
        return 0, 0
    return _deliver(user_ids, title, body, url)
