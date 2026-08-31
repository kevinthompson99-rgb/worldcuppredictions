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

from flask import current_app
from pywebpush import WebPushException, webpush

from app.extensions import db
from app.models import GameweekEntry, PushSubscription

logger = logging.getLogger(__name__)


def send_push(subscription, title, body, url="/"):
    """Send to one subscription. Returns True if delivered, False otherwise (VAPID not
    configured, the subscription is gone, or any other failure - all logged, never raised).
    """
    vapid_private_key = current_app.config.get("VAPID_PRIVATE_KEY")
    vapid_claims_email = current_app.config.get("VAPID_CLAIMS_EMAIL")
    if not vapid_private_key or not vapid_claims_email:
        logger.warning("Push not sent (VAPID keys not configured): %s", title)
        return False

    try:
        webpush(
            subscription_info={
                "endpoint": subscription.endpoint,
                "keys": {"p256dh": subscription.p256dh, "auth": subscription.auth},
            },
            data=json.dumps({"title": title, "body": body, "url": url}),
            vapid_private_key=vapid_private_key,
            vapid_claims={"sub": vapid_claims_email},
        )
        return True
    except WebPushException as exc:
        status_code = exc.response.status_code if exc.response is not None else None
        if status_code in (404, 410):
            db.session.delete(subscription)
            db.session.commit()
        else:
            logger.warning("Push send failed (endpoint=%s): %s", subscription.endpoint, exc)
        return False
    except Exception as exc:  # noqa: BLE001 - a bad send must never take down the caller
        logger.warning("Push send failed (endpoint=%s): %s", subscription.endpoint, exc)
        return False


def notify_all(title, body, url="/"):
    """Returns (sent, total) so callers can report delivery diagnostics if they want to."""
    subscriptions = PushSubscription.query.all()
    sent = sum(1 for subscription in subscriptions if send_push(subscription, title, body, url))
    return sent, len(subscriptions)


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
    subscriptions = PushSubscription.query.filter(PushSubscription.user_id.in_(user_ids)).all()
    sent = sum(1 for subscription in subscriptions if send_push(subscription, title, body, url))
    return sent, len(subscriptions)
