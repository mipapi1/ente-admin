from contextlib import contextmanager
from datetime import datetime

import pytest
from fake_smtp import FakeSMTP

from ente_admin import config
from ente_admin.db import Features
from ente_admin.notify import NotifyError
from ente_admin.server import Dashboard

DAY_US = 86400 * 1_000_000
NOON = datetime.now().replace(hour=12, minute=0, second=0, microsecond=0).timestamp()
NIGHT = NOON - 9 * 3600  # 03:00


class FakeReader:
    """What the notifier would read from museum's database, as plain data."""

    def __init__(self):
        self.features = Features()
        self.rows = []  # (user id, email, last activity µs)
        self.share_map = {}
        self.adds = []  # (album, adder, count)
        self.album_members = {}  # album -> set of user ids
        self.album_owners = {}
        self.addresses = {}

    @contextmanager
    def cursor(self):
        yield None

    def activity(self, cur):
        return list(self.rows)

    def shares(self, cur):
        return dict(self.share_map)

    def additions(self, cur, since, until):
        out, self.adds = self.adds, []
        return out

    def members(self, cur, cids):
        return ({c: self.album_members[c] for c in cids if c in self.album_members},
                {c: self.album_owners[c] for c in cids if c in self.album_owners})

    def emails(self, cur, uids):
        return {u: self.addresses[u] for u in uids if u in self.addresses}


@pytest.fixture
def smtp():
    s = FakeSMTP()
    yield s
    s.close()


@pytest.fixture
def setup(tmp_path, smtp, user_hash):
    cfg = config.load({"ENTE_ADMIN_DEMO": "true", "ENTE_ADMIN_USERS": f"admin:{user_hash}",
                       "ENTE_ADMIN_STATE_DIR": str(tmp_path),
                       "ENTE_ADMIN_NOTIFY": "send", "ENTE_ADMIN_SMTP_HOST": "127.0.0.1",
                       "ENTE_ADMIN_SMTP_PORT": str(smtp.port), "ENTE_ADMIN_SMTP_SECURITY": "none",
                       "ENTE_ADMIN_SMTP_FROM": "noreply@example.com"})
    n = Dashboard(cfg).notifier
    reader = FakeReader()
    n._reader = lambda inst: reader if inst == "photos" else None
    return n, reader


def idle(days: float, now=NOON) -> int:
    return int(now * 1e6 - days * DAY_US)


def events(n, **kw):
    return n.store.events("photos", **kw)


# ---- backup reminders ----
def test_reminders_once_per_threshold(setup, smtp):
    n, r = setup
    r.rows = [(1, "a@example.com", idle(16)), (2, "b@example.com", idle(31)), (3, "c@example.com", idle(2))]
    n.run_once(NOON)
    sent = {(e["user_id"], e["level"]) for e in events(n) if e["status"] == "sent"}
    assert sent == {(1, 15), (2, 30)}  # past both thresholds: only the highest one
    assert sorted(smtp.to()) == ["a@example.com", "b@example.com"]
    n.run_once(NOON + 60)
    assert len(smtp.messages) == 2  # not again in the same absence


def test_using_the_account_resets_reminders(setup, smtp):
    n, r = setup
    r.rows = [(1, "a@example.com", idle(16))]
    n.run_once(NOON)
    r.rows = [(1, "a@example.com", idle(16, NOON + 20 * 86400))]  # used again, then idle 16 days
    n.run_once(NOON + 20 * 86400)
    assert [e["level"] for e in events(n) if e["status"] == "sent"] == [15, 15]


def test_reminders_wait_for_the_window(setup, smtp):
    n, r = setup
    r.rows = [(1, "a@example.com", idle(16, NIGHT))]
    n.run_once(NIGHT)
    assert not smtp.messages and not events(n)
    n.run_once(NOON)
    assert len(smtp.messages) == 1


def test_shadow_mode_records_and_sends_nothing(setup, smtp):
    n, r = setup
    n.store.set_setting("mode:photos", "shadow")
    r.rows = [(1, "a@example.com", idle(16))]
    n.run_once(NOON)
    assert not smtp.messages
    assert [e["status"] for e in events(n)] == ["shadow"]


def test_off_mode_does_nothing(setup, smtp):
    n, r = setup
    n.store.set_setting("mode:photos", "off")
    r.rows = [(1, "a@example.com", idle(16))]
    n.run_once(NOON)
    assert not events(n) and "photos" not in n.last_run


def test_suppressed_account_gets_nothing(setup, smtp):
    n, r = setup
    n.store.suppress("photos", 1, "*", None, "asked", "tester")
    r.rows = [(1, "a@example.com", idle(16))]
    n.run_once(NOON)
    assert not smtp.messages
    assert [e["status"] for e in events(n)] == ["suppressed"]
    assert n.upcoming("photos", force=True)[0]["state"] == "suppressed"


def test_failure_is_retried_and_logged_once(setup, smtp):
    n, r = setup
    smtp.fail = True
    r.rows = [(1, "a@example.com", idle(16))]
    n.run_once(NOON)
    n.run_once(NOON + 60)
    assert [e["status"] for e in events(n)] == ["failed"]  # once an hour, not every round
    smtp.fail = False
    n.run_once(NOON + 120)
    assert [e["status"] for e in events(n)][0] == "sent"


def test_upcoming(setup):
    n, r = setup
    r.rows = [(1, "a@example.com", idle(3)), (2, "b@example.com", idle(20))]
    up = {u["user_id"]: u for u in n.upcoming("photos", force=True)}
    assert up[1]["state"] == "scheduled" and up[1]["next"] == 15
    assert up[2]["state"] == "due" and up[2]["next"] == 15


# ---- shares and new photos ----
def test_new_shares_only(setup, smtp):
    n, r = setup
    old = {"to": 2, "role": "VIEWER", "from_email": "a@example.com", "to_email": "b@example.com"}
    r.share_map = {"10:1:2": old}
    n.run_once(NOON)
    assert not smtp.messages  # existing shares are the baseline
    r.share_map["11:1:3"] = {"to": 3, "role": "COLLABORATOR", "from_email": "a@example.com",
                             "to_email": "c@example.com"}
    n.run_once(NOON + 60)
    n.run_once(NOON + 120)
    assert smtp.to() == ["c@example.com"]
    assert "a@example.com" in smtp.messages[0]["Subject"]


def test_new_photos_one_email_per_album_per_day(setup, smtp):
    n, r = setup
    r.album_members, r.album_owners = {7: {1, 2, 3}}, {7: 1}
    r.addresses = {1: "a@example.com", 2: "b@example.com", 3: "c@example.com"}
    n.run_once(NOON)  # baseline
    r.adds = [(7, 2, 4)]
    n.run_once(NOON + 60)
    assert sorted(smtp.to()) == ["a@example.com", "c@example.com"]  # not the one who added them
    r.adds = [(7, 2, 1)]
    n.run_once(NOON + 120)
    assert len(smtp.messages) == 2


# ---- manual actions ----
def test_send_now_and_resend(setup, smtp):
    n, r = setup
    r.rows = [(1, "a@example.com", idle(3))]
    with pytest.raises(NotifyError) as e:
        n.send_now("photos", 1, 7, "tester")
    assert e.value.status == 400
    ev = n.send_now("photos", 1, 15, "tester")
    assert ev["status"] == "sent" and ev["trigger"] == "manual" and ev["actor"] == "tester"
    again = n.resend(ev["id"], "tester")
    assert again["status"] == "sent" and again["ref"] == ev["id"]
    assert smtp.to() == ["a@example.com", "a@example.com"]
    assert n.upcoming("photos", force=True)[0]["next"] == 30  # counts as this absence's 15-day reminder


def test_test_email_goes_only_to_the_given_address(setup, smtp):
    n, r = setup
    n.store.set_setting("mode:photos", "shadow")
    r.rows = [(1, "a@example.com", idle(3))]
    ev = n.send_test("photos", "inactivity-last", "me@example.com", "tester", uid=1)
    assert ev["status"] == "sent" and ev["level"] == 30
    assert smtp.to() == ["me@example.com"]
    with pytest.raises(NotifyError):
        n.resend(ev["id"], "tester")


def test_preview_uses_the_last_threshold(setup):
    n, _ = setup
    assert "30" in n.preview("photos", "inactivity-last")["subject"]
    assert "15" in n.preview("photos", "inactivity")["subject"]


def test_import_from_ente_notify(setup, smtp):
    n, r = setup
    r.rows = [(1, "a@example.com", idle(16))]
    out = n.import_legacy("photos", {"reminders": {"photos": {"1": {"since": idle(16), "sent": [15]}}},
                                     "shares": {"photos": ["10:1:2"]}}, "tester")
    assert out == {"shares": 1, "reminders": 1}
    n.run_once(NOON)
    assert not smtp.messages  # already reminded by the old script
    with pytest.raises(NotifyError):
        n.import_legacy("photos", {"reminders": {"family": {}}}, "tester")
