from __future__ import annotations

from datetime import datetime, timezone

import pytest
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session

from job_poller.models import Application, ApplicationEvent, Base, Company, Posting
from job_poller.tracking import Status, get_application, set_status, update_application


@pytest.fixture
def session():
    engine = create_engine("sqlite:///:memory:", future=True)
    Base.metadata.create_all(engine)
    with Session(engine, expire_on_commit=False) as s:
        yield s


@pytest.fixture
def post(session):
    company = Company(name="Acme", ats="greenhouse", board_token="acme")
    session.add(company)
    session.flush()
    p = Posting(
        company_id=company.id,
        ats="greenhouse",
        external_id="1",
        title="Software Engineer Intern",
        url="https://example.com/1",
        location="Toronto, ON",
        category="intern",
        fingerprint="f",
        dedupe_key="d",
    )
    session.add(p)
    session.flush()
    return p


def events(session, application_id):
    return session.scalars(
        select(ApplicationEvent)
        .where(ApplicationEvent.application_id == application_id)
        .order_by(ApplicationEvent.id)
    ).all()


def test_first_status_creates_an_application_with_one_event(session, post):
    app = set_status(session, post.id, Status.SAVED)

    assert app.status == Status.SAVED
    assert [(e.from_status, e.to_status) for e in events(session, app.id)] == [(None, "saved")]


def test_each_transition_appends_an_event(session, post):
    set_status(session, post.id, Status.SAVED)
    set_status(session, post.id, Status.APPLIED)
    app = set_status(session, post.id, Status.ASSESSMENT)

    assert app.status == Status.ASSESSMENT
    assert [(e.from_status, e.to_status) for e in events(session, app.id)] == [
        (None, "saved"),
        ("saved", "applied"),
        ("applied", "assessment"),
    ]


def test_repeating_the_same_status_appends_no_event(session, post):
    set_status(session, post.id, Status.APPLIED)
    app = set_status(session, post.id, Status.APPLIED)

    assert len(events(session, app.id)) == 1


def test_a_posting_has_at_most_one_application(session, post):
    first = set_status(session, post.id, Status.SAVED)
    second = set_status(session, post.id, Status.APPLIED)

    assert first.id == second.id
    assert session.scalar(select(func.count()).select_from(Application)) == 1


def test_applied_at_is_stamped_on_entering_applied(session, post):
    app = set_status(session, post.id, Status.APPLIED)

    assert app.applied_at is not None


def test_applied_at_is_not_stamped_before_applying(session, post):
    app = set_status(session, post.id, Status.SAVED)

    assert app.applied_at is None


def test_applied_at_never_moves_once_set(session, post):
    original = set_status(session, post.id, Status.APPLIED).applied_at
    set_status(session, post.id, Status.REJECTED)
    app = set_status(session, post.id, Status.APPLIED)

    assert app.applied_at == original


def test_a_note_is_recorded_on_the_transition_that_carried_it(session, post):
    app = set_status(session, post.id, Status.INTERVIEWING, note="onsite Nov 3")

    assert events(session, app.id)[-1].note == "onsite Nov 3"


def test_status_for_an_unknown_posting_is_refused(session):
    with pytest.raises(ValueError, match="no posting"):
        set_status(session, 9999, Status.APPLIED)


def test_an_unrecognized_status_is_refused(session, post):
    with pytest.raises(ValueError, match="unknown status"):
        set_status(session, post.id, "ghosted")


def test_update_application_records_notes_and_next_action(session, post):
    app = set_status(session, post.id, Status.APPLIED)
    when = datetime(2026, 11, 3, tzinfo=timezone.utc)

    updated = update_application(session, app.id, notes="referred by Priya", next_action_at=when)

    assert updated.notes == "referred by Priya"
    assert updated.next_action_at == when


def test_update_application_leaves_omitted_fields_alone(session, post):
    app = set_status(session, post.id, Status.APPLIED)
    update_application(session, app.id, notes="first")

    updated = update_application(session, app.id, next_action_at=None)

    assert updated.notes == "first"


def test_get_application_is_none_for_an_untracked_posting(session, post):
    assert get_application(session, post.id) is None


def test_get_application_finds_a_tracked_posting(session, post):
    created = set_status(session, post.id, Status.SAVED)

    assert get_application(session, post.id).id == created.id
