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
