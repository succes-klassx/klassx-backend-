"""
Email notifications (spec 5.6). Uses Django's send_mail, so it works out of
the box with the console backend (prints to the terminal in dev) and only
needs EMAIL_* settings changed to send real emails via SMTP/SendGrid/etc.

SMS and in-app notifications aren't implemented — the spec left the channel
choice open (5.6), this covers the email channel as a starting point.
"""
from django.conf import settings
from django.core.mail import send_mail


def _send(to_email, subject, message):
    if not to_email:
        return
    send_mail(subject, message, settings.DEFAULT_FROM_EMAIL, [to_email], fail_silently=True)


def send_enrollment_confirmed(enrollment):
    session = enrollment.class_session
    _send(
        enrollment.student.email,
        "Votre réservation KLASSX est confirmée",
        f"Bonjour {enrollment.student.first_name},\n\n"
        f"Votre inscription à la session de {session.subject.name} "
        f"({session.get_group_tier_display()}) du {session.start_time:%d/%m/%Y à %H:%M} est confirmée.\n\n"
        f"À bientôt sur KLASSX !",
    )


def send_group_scheduled(enrollment):
    """
    Sent when an admin turns a student's pending GroupRequest into a real,
    scheduled (and usually recurring) group — the first time the student
    learns their day/time and, if recurring, that this slot repeats weekly.
    """
    session = enrollment.class_session
    recurring_note = (
        " Ce créneau se répète chaque semaine avec le même groupe et le même enseignant."
        if session.series_id
        else ""
    )
    _send(
        enrollment.student.email,
        "Votre groupe KLASSX est prêt !",
        f"Bonjour {enrollment.student.first_name},\n\n"
        f"Votre groupe de {session.subject.name} ({session.get_group_tier_display()}) est constitué. "
        f"Premier cours : {session.start_time:%d/%m/%Y à %H:%M}.{recurring_note}\n\n"
        f"Finalisez le paiement depuis votre tableau de bord pour confirmer votre place.\n\n"
        f"À bientôt sur KLASSX !",
    )


def send_waitlisted(enrollment):
    session = enrollment.class_session
    _send(
        enrollment.student.email,
        "Vous êtes sur liste d'attente — KLASSX",
        f"Bonjour {enrollment.student.first_name},\n\n"
        f"La session de {session.subject.name} du {session.start_time:%d/%m/%Y à %H:%M} est complète. "
        f"Vous avez été ajouté à la liste d'attente et serez prévenu automatiquement si une place se libère.",
    )


def send_waitlist_seat_available(enrollment):
    session = enrollment.class_session
    _send(
        enrollment.student.email,
        "Une place s'est libérée — KLASSX",
        f"Bonjour {enrollment.student.first_name},\n\n"
        f"Une place s'est libérée pour la session de {session.subject.name} "
        f"du {session.start_time:%d/%m/%Y à %H:%M}. Vous êtes maintenant inscrit.",
    )


def send_cancellation_confirmation(enrollment):
    session = enrollment.class_session
    refunded = enrollment.cancellation_reason == "cancelled_within_notice_period"
    _send(
        enrollment.student.email,
        "Annulation confirmée — KLASSX",
        f"Bonjour {enrollment.student.first_name},\n\n"
        f"Votre inscription à la session de {session.subject.name} du {session.start_time:%d/%m/%Y à %H:%M} "
        f"a été annulée. "
        + ("Vous serez remboursé sous quelques jours." if refunded else "Cette annulation tardive n'ouvre pas droit à un remboursement."),
    )


def send_session_reminder(enrollment, hours_before):
    session = enrollment.class_session
    _send(
        enrollment.student.email,
        f"Rappel : cours de {session.subject.name} dans {hours_before}",
        f"Bonjour {enrollment.student.first_name},\n\n"
        f"Votre cours de {session.subject.name} commence le {session.start_time:%d/%m/%Y à %H:%M}.\n"
        f"Lien de connexion : {session.meeting_url or '(disponible 10 minutes avant le début)'}",
    )


def send_password_reset(user, reset_url):
    _send(
        user.email,
        "Réinitialisation de votre mot de passe KLASSX",
        f"Bonjour {user.first_name},\n\n"
        f"Vous avez demandé la réinitialisation de votre mot de passe. Cliquez sur le lien ci-dessous pour en "
        f"choisir un nouveau (valable un temps limité) :\n\n{reset_url}\n\n"
        f"Si vous n'êtes pas à l'origine de cette demande, ignorez simplement cet email.",
    )


def send_group_announcement(announcement, student_emails):
    """Notifies every student in the group when their teacher posts a new announcement (see models.GroupAnnouncement)."""
    subject_name = announcement.group_assignment.subject.name
    author_name = announcement.author.get_full_name() if announcement.author else "Votre enseignant"
    for email in student_emails:
        _send(
            email,
            f"Nouveau message de votre enseignant — {subject_name}",
            f"{announcement.message}\n\n— {author_name}",
        )


def send_registration_confirmation(user):
    """
    Sent right after a student or teacher account is created
    (self-registration) — confirms the account exists, no
    email-verification link involved. For a minor student, `user.email` IS
    the parent's own email (see ParentalConsent's docstring: registering
    together with a jointly-chosen password is what constitutes consent),
    so this single email doubles as the parent's confirmation receipt —
    no separate email/step needed.
    """
    role_label = "élève" if user.role == "student" else "enseignant"
    consent = None
    if user.role == "student" and hasattr(user, "student_profile"):
        consent = getattr(user.student_profile, "parental_consent", None)

    if consent:
        student_name = f"{user.first_name} {user.last_name}".strip()
        _send(
            user.email,
            "Bienvenue sur KLASSX",
            f"Bonjour {consent.parent_full_name},\n\n"
            f"Le compte KLASSX de {student_name} (plateforme de préparation au bac) a bien été créé avec "
            f"l'adresse {user.email}.\n\n"
            f"Comme {student_name} est mineur(e), ce compte est enregistré à votre nom : c'est votre "
            f"inscription conjointe, avec un mot de passe choisi ensemble, qui vaut autorisation parentale — "
            f"aucune autre étape n'est nécessaire. Vous pouvez dès maintenant réserver et payer des cours.",
        )
        return

    _send(
        user.email,
        "Bienvenue sur KLASSX",
        f"Bonjour {user.first_name},\n\n"
        f"Votre compte {role_label} KLASSX a bien été créé avec l'adresse {user.email}.\n\n"
        f"Vous pouvez dès maintenant vous connecter et compléter votre profil.",
    )


def send_payment_method_declined(membership):
    """
    Sent when the automatic charge fails at the moment a teacher schedules
    a group (card expired, insufficient funds, etc — see
    payments.charge_saved_payment_method). The membership stays pending;
    this is the student's cue to update their card or use the manual
    "Payer" button on their dashboard instead.
    """
    series = membership.series
    _send(
        membership.student.email,
        "Le paiement de votre groupe KLASSX a échoué",
        f"Bonjour {membership.student.first_name},\n\n"
        f"Votre groupe de {series.subject.name} est planifié, mais le paiement automatique avec votre carte "
        f"enregistrée n'a pas abouti (carte expirée, fonds insuffisants...).\n\n"
        f"Merci de mettre à jour votre moyen de paiement ou de finaliser le paiement manuellement depuis votre "
        f"tableau de bord pour confirmer votre place.",
    )


def send_teacher_approved(teacher_profile):
    _send(
        teacher_profile.user.email,
        "Votre compte enseignant KLASSX est validé",
        f"Bonjour {teacher_profile.user.first_name},\n\n"
        f"Votre candidature a été validée par notre équipe. Vous pouvez désormais accéder à votre tableau de bord enseignant.",
    )


def send_group_assigned_to_teacher(assignment):
    """
    Sent when the admin assigns a formed group to a teacher (autonomous
    scheduling model — see models.GroupAssignment). This is the teacher's
    cue to log into their dashboard and pick a day/time + meeting link for
    it — no schedule exists yet at this point.
    """
    teacher_user = assignment.teacher.user
    _send(
        teacher_user.email,
        "Un nouveau groupe vous a été confié — à planifier",
        f"Bonjour {teacher_user.first_name},\n\n"
        f"Un groupe de {assignment.subject.name} ({assignment.get_group_tier_display()}) vous a été confié. "
        f"Rendez-vous sur votre tableau de bord pour choisir le jour, l'horaire et le lien de visioconférence.\n\n"
        f"À bientôt sur KLASSX !",
    )
