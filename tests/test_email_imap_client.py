from email.message import EmailMessage

import pytest

from app.collectors.email_imap_client import (
    EmailConnectionError,
    EmailAuthenticationError,
    EmailIMAPClient,
)


class _FakeIMAP:
    def __init__(
        self,
        *,
        messages: dict[str, bytes],
        auth_error: bool = False,
        mailbox_list: list[bytes] | None = None,
    ) -> None:
        self._messages = messages
        self._auth_error = auth_error
        self._mailbox_list = mailbox_list if mailbox_list is not None else [b'(\\HasNoChildren) "/" "INBOX"']
        self.stored_seen: list[str] = []

    def login(self, username: str, password: str):
        _ = (username, password)
        if self._auth_error:
            import imaplib

            raise imaplib.IMAP4.error("auth failed")
        return ("OK", [b"logged"])

    def select(self, mailbox: str):
        _ = mailbox
        return ("OK", [b"1"])

    def uid(self, command: str, *args: str):
        if command.lower() == "search":
            return ("OK", [b"1 2"])
        if command.lower() == "fetch":
            uid = args[0]
            return ("OK", [(b"RFC822", self._messages[uid])])
        if command.lower() == "store":
            uid = args[0]
            self.stored_seen.append(uid)
            return ("OK", [b"stored"])
        return ("NO", [])

    def list(self, directory: str = "", pattern: str = "*"):
        _ = (directory, pattern)
        return ("OK", self._mailbox_list)

    def close(self):
        return ("OK", [])

    def logout(self):
        return ("BYE", [])


def _message_bytes(*, from_value: str, subject: str) -> bytes:
    message = EmailMessage()
    message["From"] = from_value
    message["Subject"] = subject
    message["Message-ID"] = "<id>"
    message.set_content("body")
    return message.as_bytes()


def _run_single_subject(*, subject: str, from_value: str = "LinkedIn Jobs <jobs-noreply@linkedin.com>", body: str = "body"):
    class _OneMessageIMAP(_FakeIMAP):
        def uid(self, command: str, *args: str):
            lower = command.lower()
            if lower == "search":
                return ("OK", [b"1"])
            if lower == "fetch":
                message = EmailMessage()
                message["From"] = from_value
                message["Subject"] = subject
                message["Message-ID"] = "<id>"
                message.set_content(body)
                return ("OK", [(b"1 (RFC822 {123}", message.as_bytes()), b")"])
            return super().uid(command, *args)

        def response(self, code: str):
            if code == "UIDVALIDITY":
                return ("UIDVALIDITY", [b"13"])
            return (code, [])

    client = EmailIMAPClient(
        host="imap.gmail.com",
        port=993,
        username="user",
        password="app-password",
        folder="INBOX",
        search_days=7,
        mark_as_read=False,
        adapter=_OneMessageIMAP(messages={}),
    )
    return client.fetch_linkedin_messages_sync(
        checkpoint_uid=None,
        checkpoint_uidvalidity=None,
        incremental_enabled=False,
        bootstrap_lookback_days=7,
        bootstrap_message_limit=10,
        batch_size=10,
        rescan=True,
    )


def test_imap_fetch_filters_linkedin_alerts_and_marks_seen() -> None:
    adapter = _FakeIMAP(
        messages={
            "1": _message_bytes(from_value="jobs-noreply@linkedin.com", subject="Job alert"),
            "2": _message_bytes(from_value="other@example.com", subject="Job alert"),
        }
    )
    client = EmailIMAPClient(
        host="imap.gmail.com",
        port=993,
        username="user",
        password="app-password",
        folder="INBOX",
        search_days=7,
        mark_as_read=True,
        adapter=adapter,
    )

    messages = client.fetch_linkedin_messages()

    assert len(messages) == 1
    assert messages[0].from_address == "jobs-noreply@linkedin.com"
    assert adapter.stored_seen == ["1"]


def test_imap_authentication_error() -> None:
    client = EmailIMAPClient(
        host="imap.gmail.com",
        port=993,
        username="user",
        password="app-password",
        folder="INBOX",
        search_days=7,
        mark_as_read=False,
        adapter=_FakeIMAP(messages={}, auth_error=True),
    )

    with pytest.raises(EmailAuthenticationError):
        client.fetch_linkedin_messages()


def test_rfc2047_and_folded_headers_decoded() -> None:
    encoded_subject = "=?UTF-8?B?SmF2YSBCYWNrZW5kIGpvYiBhbGVydA==?="
    encoded_from = "=?UTF-8?B?TGlua2VkSW4gSm9icyA8am9icy1ub3JlcGx5QGxpbmtlZGluLmNvbT4=?="
    adapter = _FakeIMAP(
        messages={
            "1": _message_bytes(from_value=encoded_from, subject=encoded_subject),
            "2": _message_bytes(from_value="other@example.com", subject="Job alert"),
        },
    )
    client = EmailIMAPClient(
        host="imap.gmail.com",
        port=993,
        username="user",
        password="app-password",
        folder="INBOX",
        search_days=7,
        mark_as_read=False,
        adapter=adapter,
    )

    messages = client.fetch_linkedin_messages()
    assert len(messages) == 1
    assert "Java Backend job alert" in messages[0].subject
    assert "jobs-noreply@linkedin.com" in messages[0].from_address


def test_successful_mailbox_listing() -> None:
    adapter = _FakeIMAP(
        messages={},
        mailbox_list=[
            b'(\\HasNoChildren) "/" "INBOX"',
            b'(\\HasNoChildren) "/" "LinkedIn Jobs"',
            b'(\\HasNoChildren) "/" "[Gmail]/Sent Mail"',
        ],
    )
    client = EmailIMAPClient(
        host="imap.gmail.com",
        port=993,
        username="user",
        password="app-password",
        folder="INBOX",
        search_days=7,
        mark_as_read=False,
        adapter=adapter,
    )

    folders = client.list_mailboxes()

    assert folders == ["INBOX", "LinkedIn Jobs", "[Gmail]/Sent Mail"]


def test_empty_mailbox_list() -> None:
    adapter = _FakeIMAP(messages={}, mailbox_list=[])
    client = EmailIMAPClient(
        host="imap.gmail.com",
        port=993,
        username="user",
        password="app-password",
        folder="INBOX",
        search_days=7,
        mark_as_read=False,
        adapter=adapter,
    )

    folders = client.list_mailboxes()

    assert folders == []


def test_modified_utf7_decoding() -> None:
    adapter = _FakeIMAP(
        messages={},
        mailbox_list=[b'(\\HasNoChildren) "/" "&BB4EQgQ,BEAEMAQyBDsENQQ9BD0ESwQ1-"'],
    )
    client = EmailIMAPClient(
        host="imap.gmail.com",
        port=993,
        username="user",
        password="app-password",
        folder="INBOX",
        search_days=7,
        mark_as_read=False,
        adapter=adapter,
    )

    folders = client.list_mailboxes()

    assert folders == ["Отправленные"]


def test_connection_failure(monkeypatch) -> None:
    import imaplib

    def _raise(*args, **kwargs):
        _ = (args, kwargs)
        raise OSError("cannot connect")

    monkeypatch.setattr(imaplib, "IMAP4_SSL", _raise)
    client = EmailIMAPClient(
        host="imap.gmail.com",
        port=993,
        username="user",
        password="app-password",
        folder="INBOX",
        search_days=7,
        mark_as_read=False,
        adapter=None,
    )

    with pytest.raises(EmailConnectionError):
        client.list_mailboxes()


def test_incremental_uid_search_falls_back_to_all_filter_when_server_returns_stale_uid() -> None:
    class _UidFallbackIMAP(_FakeIMAP):
        def uid(self, command: str, *args: str):
            if command.lower() == "search":
                if args and str(args[0]).upper() == "UID":
                    # Simulate provider bug: UID range query returns stale UID.
                    return ("OK", [b"51"])
                return ("OK", [b"1 2 3 51"])
            if command.lower() == "fetch":
                raise AssertionError("No fetch expected when filtered result is empty")
            return super().uid(command, *args)

        def response(self, code: str):
            if code == "UIDVALIDITY":
                return ("UIDVALIDITY", [b"13"])
            return (code, [])

    client = EmailIMAPClient(
        host="imap.gmail.com",
        port=993,
        username="user",
        password="app-password",
        folder="INBOX",
        search_days=7,
        mark_as_read=False,
        adapter=_UidFallbackIMAP(messages={}),
    )

    result = client.fetch_linkedin_messages_sync(
        checkpoint_uid=51,
        checkpoint_uidvalidity="13",
        incremental_enabled=True,
        bootstrap_lookback_days=7,
        bootstrap_message_limit=500,
        batch_size=200,
        rescan=False,
    )
    assert result.mode == "incremental"
    assert result.messages_fetched == 0
    assert result.search_criteria.endswith("(fallback=ALL-filter)")


def test_fetch_supports_nested_and_trailing_imap_response_shapes() -> None:
    message = _message_bytes(from_value="LinkedIn Jobs <jobs-noreply@linkedin.com>", subject="Job alert")

    class _ShapeIMAP(_FakeIMAP):
        def uid(self, command: str, *args: str):
            lower = command.lower()
            if lower == "search":
                return ("OK", [b"1 2 3 4"])
            if lower == "fetch":
                uid = str(args[0])
                if uid == "1":
                    return ("OK", [(b"1 (RFC822 {123}", message)])
                if uid == "2":
                    return ("OK", [(b"2 (RFC822 {123}", [message])])
                if uid == "3":
                    return ("OK", [None, (b"3 (RFC822 {123}", message), b")"])
                return ("OK", [b")", (b"4 (RFC822 {123}", (None, message))])
            return super().uid(command, *args)

        def response(self, code: str):
            if code == "UIDVALIDITY":
                return ("UIDVALIDITY", [b"13"])
            return (code, [])

    client = EmailIMAPClient(
        host="imap.gmail.com",
        port=993,
        username="user",
        password="app-password",
        folder="INBOX",
        search_days=7,
        mark_as_read=False,
        adapter=_ShapeIMAP(messages={}),
    )

    result = client.fetch_linkedin_messages_sync(
        checkpoint_uid=None,
        checkpoint_uidvalidity=None,
        incremental_enabled=False,
        bootstrap_lookback_days=7,
        bootstrap_message_limit=100,
        batch_size=100,
        rescan=True,
    )
    assert result.fetch_succeeded == 4
    assert result.decode_succeeded == 4
    assert result.messages_matched == 4
    assert result.rejected_sender == 0
    assert result.rejected_subject == 0


def test_display_name_sender_and_encoded_subject_are_matched() -> None:
    encoded_subject = "=?UTF-8?B?Sm9iIGFsZXJ0OiBKYXZh?="
    message = _message_bytes(
        from_value="LinkedIn Jobs <jobs-listings@linkedin.com>",
        subject=encoded_subject,
    )

    class _OneIMAP(_FakeIMAP):
        def uid(self, command: str, *args: str):
            if command.lower() == "search":
                return ("OK", [b"10"])
            if command.lower() == "fetch":
                return ("OK", [(b"10 (RFC822 {123}", message), b")"])
            return super().uid(command, *args)

        def response(self, code: str):
            if code == "UIDVALIDITY":
                return ("UIDVALIDITY", [b"13"])
            return (code, [])

    client = EmailIMAPClient(
        host="imap.gmail.com",
        port=993,
        username="user",
        password="app-password",
        folder="INBOX",
        search_days=7,
        mark_as_read=False,
        adapter=_OneIMAP(messages={}),
    )
    result = client.fetch_linkedin_messages_sync(
        checkpoint_uid=None,
        checkpoint_uidvalidity=None,
        incremental_enabled=False,
        bootstrap_lookback_days=7,
        bootstrap_message_limit=10,
        batch_size=10,
        rescan=True,
    )
    assert result.messages_matched == 1
    assert result.rejected_sender == 0
    assert result.rejected_subject == 0


def test_non_linkedin_messages_rejected_with_balanced_counters() -> None:
    linkedin = _message_bytes(from_value="LinkedIn Jobs <jobs-noreply@linkedin.com>", subject="Job alert")
    wrong_sender = _message_bytes(from_value="alerts@example.com", subject="Job alert")
    wrong_subject = _message_bytes(from_value="LinkedIn Jobs <jobs-noreply@linkedin.com>", subject="Newsletter")
    malformed = b"this-is-not-an-email"

    class _MixedIMAP(_FakeIMAP):
        def uid(self, command: str, *args: str):
            if command.lower() == "search":
                return ("OK", [b"1 2 3 4"])
            if command.lower() == "fetch":
                uid = str(args[0])
                if uid == "1":
                    return ("OK", [(b"1 (RFC822 {123}", linkedin)])
                if uid == "2":
                    return ("OK", [(b"2 (RFC822 {123}", wrong_sender)])
                if uid == "3":
                    return ("OK", [(b"3 (RFC822 {123}", wrong_subject)])
                return ("OK", [(b"4 (RFC822 {123}", malformed)])
            return super().uid(command, *args)

        def response(self, code: str):
            if code == "UIDVALIDITY":
                return ("UIDVALIDITY", [b"13"])
            return (code, [])

    client = EmailIMAPClient(
        host="imap.gmail.com",
        port=993,
        username="user",
        password="app-password",
        folder="INBOX",
        search_days=7,
        mark_as_read=False,
        adapter=_MixedIMAP(messages={}),
    )
    result = client.fetch_linkedin_messages_sync(
        checkpoint_uid=None,
        checkpoint_uidvalidity=None,
        incremental_enabled=False,
        bootstrap_lookback_days=7,
        bootstrap_message_limit=20,
        batch_size=20,
        rescan=True,
    )
    assert result.searched_uids == 4
    assert result.fetch_attempted == 4
    assert result.fetch_succeeded == 3
    assert result.decode_succeeded == 3
    assert result.rejected_sender == 1
    assert result.rejected_subject == 1
    assert result.messages_matched == 1
    assert len(result.rejection_events) == 3
    accounted = result.messages_matched + result.rejected_sender + result.rejected_subject + (result.fetch_attempted - result.fetch_succeeded)
    assert accounted == result.fetch_attempted


def test_linkedin_body_link_allows_nonstandard_subject() -> None:
    message = EmailMessage()
    message["From"] = "LinkedIn Jobs <jobs-noreply@linkedin.com>"
    message["Subject"] = "Career update"
    message["Message-ID"] = "<id>"
    message.set_content("Check this opportunity: https://www.linkedin.com/jobs/view/123/")

    class _BodyLinkIMAP(_FakeIMAP):
        def uid(self, command: str, *args: str):
            if command.lower() == "search":
                return ("OK", [b"1"])
            if command.lower() == "fetch":
                return ("OK", [(b"1 (RFC822 {123}", message.as_bytes())])
            return super().uid(command, *args)

        def response(self, code: str):
            if code == "UIDVALIDITY":
                return ("UIDVALIDITY", [b"13"])
            return (code, [])

    client = EmailIMAPClient(
        host="imap.gmail.com",
        port=993,
        username="user",
        password="app-password",
        folder="INBOX",
        search_days=7,
        mark_as_read=False,
        adapter=_BodyLinkIMAP(messages={}),
    )
    result = client.fetch_linkedin_messages_sync(
        checkpoint_uid=None,
        checkpoint_uidvalidity=None,
        incremental_enabled=False,
        bootstrap_lookback_days=7,
        bootstrap_message_limit=10,
        batch_size=10,
        rescan=True,
    )
    assert result.messages_matched == 1
    assert result.rejected_subject == 0


@pytest.mark.parametrize(
    ("subject", "expected_reason"),
    [
        ("Software Engineer - Backend (Remote) at Hire Feed", "accepted_role_at_company"),
        ("Java Developer - AI/ML (Remote) at Hire Feed", "accepted_role_at_company"),
        ('"JVM Backend posted in the past 24..."', "accepted_posted_alert"),
        ("Tyler Technologies - Senior Software Engineer posted on 7/18/26", "accepted_posted_on"),
        ('Ksenia, apply now to "PH - Spring Boot Software Engineer (6-month Contract) at ..."', "accepted_apply_now"),
        ("Senior Java Developer (Remote) at Quik Hire Staffing", "accepted_role_at_company"),
        ("Java Developer at Bonhill Partners: up to £200K/year", "accepted_salary"),
        ("Backend Developer | $70/hr Remote at Crossing Hurdles: up to $70/hour", "accepted_salary"),
        ("Java Developer at Bonhill Partners: up to $200K/year", "accepted_salary"),
        ("=?UTF-8?Q?Software_Engineer_-_Backend_(Remote)_at_Hire_Feed?=", "accepted_role_at_company"),
        ("“Kotlin Backend posted in the past 24...”", "accepted_posted_alert"),
        ("Software Engineer &amp; Backend at Hire Feed", "accepted_role_at_company"),
    ],
)
def test_job_subject_examples_are_accepted(subject: str, expected_reason: str) -> None:
    result = _run_single_subject(subject=subject)
    assert result.messages_matched == 1
    assert result.rejected_subject == 0
    assert result.classification_counts.get(expected_reason) == 1


def test_rejects_linkedin_security_message() -> None:
    result = _run_single_subject(subject="Security alert: new sign-in to your account")
    assert result.messages_matched == 0
    assert result.rejected_subject == 1
    assert result.classification_counts.get("rejected_security") == 1


def test_rejects_linkedin_connection_message() -> None:
    result = _run_single_subject(subject="You have a new connection request")
    assert result.messages_matched == 0
    assert result.rejected_subject == 1
    assert result.classification_counts.get("rejected_social") == 1


def test_rejects_linkedin_marketing_newsletter() -> None:
    result = _run_single_subject(subject="LinkedIn Newsletter: Top Voices this week")
    assert result.messages_matched == 0
    assert result.rejected_subject == 1
    assert result.classification_counts.get("rejected_marketing") == 1


def test_trusted_sender_without_job_evidence_is_rejected() -> None:
    result = _run_single_subject(subject="Weekly summary update from LinkedIn")
    assert result.messages_matched == 0
    assert result.rejected_subject == 1
    assert result.classification_counts.get("rejected_no_job_evidence") == 1


def test_folded_multiline_subject_is_accepted() -> None:
    raw = (
        b"From: LinkedIn Jobs <jobs-noreply@linkedin.com>\r\n"
        b"Subject: Java Developer at Bonhill Partners:\r\n"
        b" posted on 7/18/26\r\n"
        b"Message-ID: <id>\r\n"
        b"\r\nbody\r\n"
    )

    class _FoldedSubjectIMAP(_FakeIMAP):
        def uid(self, command: str, *args: str):
            if command.lower() == "search":
                return ("OK", [b"1"])
            if command.lower() == "fetch":
                return ("OK", [(b"1 (RFC822 {123}", raw), b")"])
            return super().uid(command, *args)

        def response(self, code: str):
            if code == "UIDVALIDITY":
                return ("UIDVALIDITY", [b"13"])
            return (code, [])

    client = EmailIMAPClient(
        host="imap.gmail.com",
        port=993,
        username="user",
        password="app-password",
        folder="INBOX",
        search_days=7,
        mark_as_read=False,
        adapter=_FoldedSubjectIMAP(messages={}),
    )
    result = client.fetch_linkedin_messages_sync(
        checkpoint_uid=None,
        checkpoint_uidvalidity=None,
        incremental_enabled=False,
        bootstrap_lookback_days=7,
        bootstrap_message_limit=10,
        batch_size=10,
        rescan=True,
    )
    assert result.messages_matched == 1
    assert result.classification_counts.get("accepted_posted_on") == 1


def test_twenty_five_fetched_messages_are_fully_accounted() -> None:
    linkedin = _message_bytes(from_value="LinkedIn Jobs <jobs-noreply@linkedin.com>", subject="Job alert")
    other = _message_bytes(from_value="noreply@example.com", subject="Digest")

    class _TwentyFiveIMAP(_FakeIMAP):
        def uid(self, command: str, *args: str):
            if command.lower() == "search":
                return ("OK", [b" ".join(str(i).encode("ascii") for i in range(1, 26))])
            if command.lower() == "fetch":
                uid = int(str(args[0]))
                payload = linkedin if uid % 2 == 0 else other
                return ("OK", [(f"{uid} (RFC822 {{123}}".encode("ascii"), payload), b")"])
            return super().uid(command, *args)

        def response(self, code: str):
            if code == "UIDVALIDITY":
                return ("UIDVALIDITY", [b"13"])
            return (code, [])

    client = EmailIMAPClient(
        host="imap.gmail.com",
        port=993,
        username="user",
        password="app-password",
        folder="INBOX",
        search_days=7,
        mark_as_read=False,
        adapter=_TwentyFiveIMAP(messages={}),
    )
    result = client.fetch_linkedin_messages_sync(
        checkpoint_uid=None,
        checkpoint_uidvalidity=None,
        incremental_enabled=False,
        bootstrap_lookback_days=7,
        bootstrap_message_limit=30,
        batch_size=30,
        rescan=True,
    )
    assert result.searched_uids == 25
    assert result.fetch_attempted == 25
    assert result.fetch_succeeded == 25
    assert result.decode_succeeded == 25
    assert result.rejected_sender == 13
    assert result.rejected_subject == 0
    assert result.messages_matched == 12
    assert result.messages_fetched == 25
