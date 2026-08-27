"""
Stripe integration (Module 3 of the spec).

Two payment flows:
- One-off Checkout Session per class session enrollment.
- Recurring Checkout Session (subscription) for the Maths video capsules.

A webhook handler confirms payment asynchronously — this is deliberate:
Stripe recommends never trusting the browser redirect alone to mark
something as paid, since the user can close the tab before the redirect.

⚠️ Requires a real STRIPE_SECRET_KEY (and STRIPE_WEBHOOK_SECRET once you
register the webhook in the Stripe dashboard) to actually work — without
them, these functions will raise stripe.error.AuthenticationError. Get keys
from https://dashboard.stripe.com/apikeys.
"""
import stripe
from django.conf import settings

stripe.api_key = settings.STRIPE_SECRET_KEY


def get_or_create_stripe_customer(student_profile):
    """Returns this student's Stripe Customer id, creating one via the API the first time they need one."""
    if student_profile.stripe_customer_id:
        return student_profile.stripe_customer_id
    customer = stripe.Customer.create(
        email=student_profile.user.email, name=student_profile.user.get_full_name() or student_profile.user.username
    )
    student_profile.stripe_customer_id = customer.id
    student_profile.save(update_fields=["stripe_customer_id"])
    return customer.id


def create_card_setup_checkout_session(student_profile):
    """
    Checkout Session in mode="setup" — collects and SAVES a card without
    charging anything. This is the "enter your card now, get billed later"
    flow: a student adds a card right when requesting a group package, and
    is only actually charged once their teacher schedules a real session
    (see charge_saved_payment_method below) — never at request time.

    The resulting payment method is attached as this student's Stripe
    Customer default via the checkout.session.completed webhook handler
    (StripeWebhookView — mode="setup" sessions fire that same event, kind
    "card_setup" in metadata).
    """
    customer_id = get_or_create_stripe_customer(student_profile)
    return stripe.checkout.Session.create(
        mode="setup",
        payment_method_types=["card"],
        customer=customer_id,
        metadata={"student_profile_id": str(student_profile.id), "kind": "card_setup"},
        success_url=f"{settings.FRONTEND_URL}/catalogue?card=success",
        cancel_url=f"{settings.FRONTEND_URL}/catalogue?card=cancelled",
    )


def charge_saved_payment_method(membership):
    """
    Called when a teacher schedules a group (GroupAssignmentViewSet.schedule)
    for a student who already saved a card — creates the real monthly
    Stripe Subscription right then, off-session (the browser isn't
    involved at all; it's the teacher's action that triggers this, not the
    student's). This is what actually bills the student — no separate
    manual step needed once a card is on file.

    Returns the created Subscription on success. Returns None (no-op,
    caller should fall back to the manual "Payer" button / the existing
    `checkout` action) if this student has no saved payment method yet.
    Raises stripe.error.CardError if Stripe declines the off-session
    charge (expired/insufficient funds/etc) — caller should catch this,
    leave the membership pending, and prompt the student to fix their
    payment method; see notifications.send_payment_method_declined.
    """
    student_profile = membership.student.student_profile
    if not student_profile.has_payment_method:
        return None
    series = membership.series
    return stripe.Subscription.create(
        customer=student_profile.stripe_customer_id,
        default_payment_method=student_profile.stripe_default_payment_method_id,
        items=[{
            "price_data": {
                "currency": "eur",
                "unit_amount": membership.monthly_price_cents,
                "recurring": {"interval": "month"},
                "product_data": {
                    "name": f"{series.subject.name} — {series.get_group_tier_display()} — groupe hebdomadaire",
                },
            },
        }],
        off_session=True,
        metadata={"membership_id": str(membership.id), "kind": "series_membership"},
        expand=["latest_invoice.payment_intent"],
    )


def create_enrollment_checkout_session(enrollment, unit_amount_cents, currency="eur"):
    """
    Creates a Stripe Checkout Session for a single class session booking.
    `unit_amount_cents` should come from core/pricing.py (session_price_cents),
    which the caller is expected to have already computed.
    """
    session = stripe.checkout.Session.create(
        mode="payment",
        payment_method_types=["card"],
        customer_email=enrollment.student.email,
        line_items=[{
            "price_data": {
                "currency": currency,
                "unit_amount": unit_amount_cents,
                "product_data": {
                    "name": f"{enrollment.class_session.subject.name} — {enrollment.class_session.get_group_tier_display()}",
                },
            },
            "quantity": 1,
        }],
        metadata={"enrollment_id": str(enrollment.id), "kind": "enrollment"},
        success_url=f"{settings.FRONTEND_URL}/tableau-de-bord?payment=success",
        cancel_url=f"{settings.FRONTEND_URL}/catalogue?payment=cancelled",
    )
    return session


def create_subscription_checkout_session(user, plan, unit_amount_cents_override=None):
    """
    Creates a Stripe Checkout Session for a self-study content
    subscription (spec: videos + PDF, no teacher — see
    models.SelfStudyPlan/Subscription). Each of the 5 plans is billed
    completely independently — this is why `price_data` is built from
    `plan.price_cents` directly rather than a single shared, pre-created
    Stripe Price object: 5 separate plans need 5 separate recurring
    prices, and building them inline means changing a price in the admin
    (SelfStudyPlan.price_cents) takes effect immediately, no Stripe
    dashboard step required.

    `unit_amount_cents_override`: pass a discounted amount (see
    core/discounts.py) to charge that instead of plan.price_cents as-is.
    """
    session = stripe.checkout.Session.create(
        mode="subscription",
        payment_method_types=["card"],
        customer_email=user.email,
        line_items=[{
            "price_data": {
                "currency": "eur",
                "unit_amount": unit_amount_cents_override if unit_amount_cents_override is not None else plan.price_cents,
                "recurring": {"interval": "month"},
                "product_data": {"name": f"KLASSX — {plan.name}"},
            },
            "quantity": 1,
        }],
        metadata={"user_id": str(user.id), "plan_id": str(plan.id), "kind": "subscription"},
        success_url=f"{settings.FRONTEND_URL}/tableau-de-bord?subscription=success",
        cancel_url=f"{settings.FRONTEND_URL}/catalogue?subscription=cancelled",
    )
    return session


def create_series_subscription_checkout_session(membership, unit_amount_cents_override=None):
    """
    Creates a Stripe Checkout Session for a student's monthly subscription
    to a fixed recurring group (spec: pay monthly, auto-renews, 2 weeks'
    notice to leave). Unlike the video capsule subscription, the price
    varies per group (tier x session length), so it's built with
    `price_data.recurring` rather than a pre-created Stripe Price object.

    `unit_amount_cents_override`: pass a discounted amount (see
    core/discounts.py — only the global discount applies here, there's
    no promo-code entry point in the group flow) to charge that instead
    of membership.monthly_price_cents as-is.
    """
    series = membership.series
    session = stripe.checkout.Session.create(
        mode="subscription",
        payment_method_types=["card"],
        customer_email=membership.student.email,
        line_items=[{
            "price_data": {
                "currency": "eur",
                "unit_amount": unit_amount_cents_override if unit_amount_cents_override is not None else membership.monthly_price_cents,
                "recurring": {"interval": "month"},
                "product_data": {
                    "name": f"{series.subject.name} — {series.get_group_tier_display()} — groupe hebdomadaire",
                },
            },
            "quantity": 1,
        }],
        metadata={"membership_id": str(membership.id), "kind": "series_membership"},
        success_url=f"{settings.FRONTEND_URL}/tableau-de-bord?membership=success",
        cancel_url=f"{settings.FRONTEND_URL}/tableau-de-bord?membership=cancelled",
    )
    return session


def cancel_stripe_subscription(stripe_subscription_id, at_period_end=True):
    """Cancels a Stripe subscription — used when a SeriesMembership's 2-week notice period ends."""
    if not stripe_subscription_id:
        return
    stripe.Subscription.modify(stripe_subscription_id, cancel_at_period_end=at_period_end) if at_period_end \
        else stripe.Subscription.delete(stripe_subscription_id)


def construct_webhook_event(payload, sig_header):
    """Verifies the Stripe webhook signature and returns the parsed event."""
    return stripe.Webhook.construct_event(payload, sig_header, settings.STRIPE_WEBHOOK_SECRET)


def refund_payment(payment):
    """
    Issues a full refund for a succeeded Payment via its PaymentIntent.
    Returns None (no-op) if there's nothing refundable — the payment never
    succeeded, was already refunded, or has no `stripe_payment_intent_id`
    yet (e.g. the Checkout Session was created but the webhook confirming
    it hasn't landed — that shouldn't happen for anything actually marked
    "paid" in our DB, but this guards against it defensively).

    Raises stripe.error.StripeError on any actual Stripe-side failure —
    callers should catch this so a refund failure doesn't block the
    cancellation itself from going through; see refund_enrollment_if_paid
    below for how it's normally used.
    """
    if payment.status != "succeeded" or not payment.stripe_payment_intent_id:
        return None
    return stripe.Refund.create(payment_intent=payment.stripe_payment_intent_id)


def refund_enrollment_if_paid(enrollment):
    """
    Refunds the enrollment's most recent succeeded Payment (if any) and
    marks both the Payment and the Enrollment accordingly. Used by both:
    - EnrollmentViewSet.cancel (student/admin-initiated) — only called
      when the cancellation is within the notice period, since a late
      cancellation isn't eligible (spec 5.2).
    - cancel_undersubscribed_sessions — always called, since that
      cancellation is KLASSX's fault, not the student's, so the
      late-cancellation-no-refund rule doesn't apply there at all.

    No-op if the enrollment isn't marked "paid", or has no succeeded
    Payment row to refund (shouldn't normally happen together, but this
    stays defensive rather than assuming). Swallows Stripe errors — a
    refund failure must not block the cancellation itself; it's up to the
    caller to log/surface it (e.g. via logging or an admin notification)
    since this function only returns True/False, it doesn't raise.
    """
    import logging
    from ..models import Enrollment, Payment

    logger = logging.getLogger(__name__)

    if enrollment.payment_status != Enrollment.PaymentStatus.PAID:
        return False
    payment = Payment.objects.filter(enrollment=enrollment, status=Payment.Status.SUCCEEDED).order_by("-created_at").first()
    if not payment:
        return False

    try:
        refund_payment(payment)
    except Exception:
        logger.exception("Stripe refund failed for enrollment %s / payment %s", enrollment.id, payment.id)
        return False

    payment.status = Payment.Status.REFUNDED
    payment.save(update_fields=["status"])
    enrollment.payment_status = Enrollment.PaymentStatus.REFUNDED
    enrollment.save(update_fields=["payment_status"])
    return True
