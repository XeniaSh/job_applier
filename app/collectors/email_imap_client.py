from __future__ import annotations

import imaplib
import logging
import re
import ssl
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from email import message_from_bytes
from email.header import decode_header, make_header
from email.message import Message
from email.utils import parsedate_to_datetime
from typing import Protocol

logger = logging.getLogger(__name__)


class EmailConnectionError(Exception):
    """Raised when IMAP server connection fails."""


class EmailAuthenticationError(Exception):
    """Raised when IMAP authentication fails."""


class EmailMessageError(Exception):
    """Raised when IMAP message parsing fails."""


@dataclass(frozen=True)
class RawEmailMessage:
    uid: str
    message_id: str
    from_address: str
    subject: str
    received_at: datetime | None
    email_message: Message


@dataclass(frozen=True)
class ImapSyncResult:
    mode: str
    checkpoint_before: int | None
    checkpoint_after: int | None
    highest_uid_seen: int | None
    uidvalidity: str | None
    uidvalidity_changed: bool
    messages_matched: int
    messages_fetched: int
    search_criteria: str
    timings_ms: dict[str, int]
    messages: list[RawEmailMessage]


class IMAPAdapter(Protocol):
    def login(self, username: str, password: str): ...

    def list(self, directory: str = "", pattern: str = "*"): ...

    def select(self, mailbox: str): ...

    def uid(self, command: str, *args): ...

    def close(self): ...

    def logout(self): ...

    def response(self, code: str): ...


class EmailIMAPClient:
    SUBJECT_INDICATORS = (
        "job alert",
        "jobs you may be interested in",
        "new jobs",
        "вакансии",
        "новые вакансии",
    )

    def __init__(
        self,
        host: str,
        port: int,
        username: str,
        password: str,
        folder: str,
        search_days: int,
        mark_as_read: bool,
        adapter: IMAPAdapter | None = None,
    ) -> None:
        self._host = host
        self._port = port
        self._username = username
        self._password = password
        self._folder = folder
        self._search_days = search_days
        self._mark_as_read = mark_as_read
        self._provided_adapter = adapter

    @property
    def folder(self) -> str:
        return self._folder

    @property
    def username(self) -> str:
        return self._username

    def fetch_linkedin_messages(self) -> list[RawEmailMessage]:
        result = self.fetch_linkedin_messages_sync(
            checkpoint_uid=None,
            checkpoint_uidvalidity=None,
            incremental_enabled=False,
            bootstrap_lookback_days=self._search_days,
            bootstrap_message_limit=500,
            batch_size=500,
            rescan=False,
        )
        return result.messages

    def fetch_linkedin_messages_sync(
        self,
        *,
        checkpoint_uid: int | None,
        checkpoint_uidvalidity: str | None,
        incremental_enabled: bool,
        bootstrap_lookback_days: int,
        bootstrap_message_limit: int,
        batch_size: int,
        rescan: bool,
    ) -> ImapSyncResult:
        timings_ms: dict[str, int] = {}
        start_connect = time.monotonic()
        client = self._open_authenticated_client()
        timings_ms["connect"] = max(0, int((time.monotonic() - start_connect) * 1000))
        mode = "incremental"
        start_select = time.monotonic()
        try:
            status, _ = client.select(f'"{self._folder}"')
            if status != "OK":
                raise EmailConnectionError(
                    f"Cannot select mailbox folder: {self._folder}"
                )
            timings_ms["select"] = max(0, int((time.monotonic() - start_select) * 1000))

            uidvalidity = self._read_uidvalidity(client)
            uidvalidity_changed = checkpoint_uidvalidity is not None and uidvalidity is not None and checkpoint_uidvalidity != uidvalidity
            if rescan:
                mode = "rescan"
            elif not incremental_enabled or checkpoint_uid is None or uidvalidity_changed:
                mode = "bootstrap"

            start_search = time.monotonic()
            if mode == "incremental" and checkpoint_uid is not None:
                uids, search_criteria = self._search_uids_newer_than(client=client, last_uid=checkpoint_uid)
            else:
                uids, search_criteria = self._search_uids_since_days(client=client, days=max(1, bootstrap_lookback_days))
                if bootstrap_message_limit > 0:
                    uids = uids[-bootstrap_message_limit:]
            timings_ms["search"] = max(0, int((time.monotonic() - start_search) * 1000))

            if batch_size > 0:
                uids = uids[:batch_size]

            highest_uid_seen = max(uids) if uids else checkpoint_uid
            if not uids:
                return ImapSyncResult(
                    mode=mode,
                    checkpoint_before=checkpoint_uid,
                    checkpoint_after=checkpoint_uid,
                    highest_uid_seen=highest_uid_seen,
                    uidvalidity=uidvalidity,
                    uidvalidity_changed=uidvalidity_changed,
                    messages_matched=0,
                    messages_fetched=0,
                    search_criteria=search_criteria,
                    timings_ms=timings_ms,
                    messages=[],
                )

            start_fetch = time.monotonic()
            messages: list[RawEmailMessage] = []
            for uid in sorted(uids):
                try:
                    raw_message = self._fetch_message_by_uid(
                        client=client,
                        uid=str(uid),
                    )
                except EmailMessageError:
                    logger.warning(
                        "Failed to parse email message UID=%s",
                        uid,
                    )
                    continue

                if not self._is_likely_linkedin_alert(
                    raw_message.from_address,
                    raw_message.subject,
                ):
                    continue

                messages.append(raw_message)

                if self._mark_as_read:
                    client.uid(
                        "store",
                        str(uid),
                        "+FLAGS",
                        "(\\Seen)",
                    )
            timings_ms["fetch"] = max(0, int((time.monotonic() - start_fetch) * 1000))

            return ImapSyncResult(
                mode=mode,
                checkpoint_before=checkpoint_uid,
                checkpoint_after=highest_uid_seen,
                highest_uid_seen=highest_uid_seen,
                uidvalidity=uidvalidity,
                uidvalidity_changed=uidvalidity_changed,
                messages_matched=len(messages),
                messages_fetched=len(uids),
                search_criteria=search_criteria,
                timings_ms=timings_ms,
                messages=messages,
            )

        finally:
            self._cleanup_client(client)

    def list_mailboxes(self) -> list[str]:
        client = self._open_authenticated_client()

        try:
            try:
                status, data = client.list()
            except imaplib.IMAP4.error as exc:
                raise EmailConnectionError(
                    f"Cannot list IMAP mailboxes: {exc}"
                ) from exc

            if status != "OK":
                raise EmailConnectionError("Cannot list IMAP mailboxes.")

            raw_entries = [
                item
                for item in (data or [])
                if isinstance(item, (bytes, bytearray, str))
            ]

            folders = [_parse_mailbox_name(item) for item in raw_entries]

            result = [folder for folder in folders if folder]

            logger.info(
                "Retrieved %d IMAP mailboxes",
                len(result),
            )

            return result

        finally:
            self._cleanup_client(client)

    def _fetch_message_by_uid(
        self,
        client: IMAPAdapter,
        uid: str,
    ) -> RawEmailMessage:
        status, data = client.uid(
            "fetch",
            uid,
            "(RFC822)",
        )

        if status != "OK" or not data:
            raise EmailMessageError(f"Cannot fetch message UID={uid}")

        raw_bytes: bytes | None = None

        for item in data:
            if isinstance(item, tuple) and len(item) == 2:
                payload = item[1]
                if isinstance(payload, bytes):
                    raw_bytes = payload
                    break

        if raw_bytes is None:
            raise EmailMessageError(f"Message UID={uid} has invalid payload")

        try:
            parsed = message_from_bytes(raw_bytes)
        except Exception as exc:  # noqa: BLE001
            raise EmailMessageError(f"Message UID={uid} parse failed") from exc

        from_address = str(parsed.get("From", ""))
        subject = str(parsed.get("Subject", ""))
        message_id = str(parsed.get("Message-ID", "")).strip()

        received_at: datetime | None = None
        raw_date = parsed.get("Date")

        if raw_date:
            try:
                received_at = parsedate_to_datetime(raw_date)
            except (TypeError, ValueError, OverflowError):
                received_at = None

        return RawEmailMessage(
            uid=uid,
            message_id=message_id or uid,
            from_address=_decode_header_value(from_address),
            subject=_decode_header_value(subject),
            received_at=received_at,
            email_message=parsed,
        )

    def _is_likely_linkedin_alert(
        self,
        from_address: str,
        subject: str,
    ) -> bool:
        from_lower = from_address.lower()
        subject_lower = subject.lower()

        if "linkedin.com" not in from_lower:
            return False

        return any(indicator in subject_lower for indicator in self.SUBJECT_INDICATORS)

    def _open_authenticated_client(self) -> IMAPAdapter:
        client = self._provided_adapter

        if client is None:
            try:
                context = ssl.create_default_context()
                client = imaplib.IMAP4_SSL(
                    self._host,
                    self._port,
                    ssl_context=context,
                )
            except (OSError, ssl.SSLError) as exc:
                raise EmailConnectionError("IMAP connection failed.") from exc

        try:
            client.login(
                self._username,
                self._password,
            )
        except imaplib.IMAP4.error as exc:
            self._cleanup_client(client)
            raise EmailAuthenticationError("IMAP authentication failed.") from exc

        logger.info("Connected to IMAP")

        return client

    def _search_uids_newer_than(self, *, client: IMAPAdapter, last_uid: int) -> tuple[list[int], str]:
        start_uid = max(1, int(last_uid) + 1)
        criteria = f"UID {start_uid}:*"
        status, data = client.uid(
            "search",
            "UID",
            f"{start_uid}:*",
        )
        if status != "OK" or not data:
            return [], criteria
        uids = _parse_uid_list(data[0] if data else b"")
        # Some providers respond to UID range search with stale/invalid UIDs.
        # Fallback to ALL+local filtering to preserve incremental semantics.
        if uids and min(uids) <= int(last_uid):
            fallback_status, fallback_data = client.uid("search", "ALL")
            if fallback_status != "OK" or not fallback_data:
                return [], criteria
            all_uids = _parse_uid_list(fallback_data[0] if fallback_data else b"")
            filtered = [uid for uid in all_uids if uid > int(last_uid)]
            return filtered, f"{criteria} (fallback=ALL-filter)"
        return uids, criteria

    def _search_uids_since_days(self, *, client: IMAPAdapter, days: int) -> tuple[list[int], str]:
        since_date = (
            datetime.now(timezone.utc) - timedelta(days=max(1, int(days)))
        ).strftime("%d-%b-%Y")
        criteria = f'SINCE "{since_date}"'
        status, data = client.uid(
            "search",
            "SINCE",
            since_date,
        )
        if status != "OK" or not data:
            return [], criteria
        return _parse_uid_list(data[0] if data else b""), criteria

    def _read_uidvalidity(self, client: IMAPAdapter) -> str | None:
        response_method = getattr(client, "response", None)
        if not callable(response_method):
            return None
        try:
            _code, values = response_method("UIDVALIDITY")
        except Exception:  # noqa: BLE001
            return None
        if not values:
            return None
        first = values[0]
        if isinstance(first, bytes):
            return first.decode("utf-8", errors="ignore").strip() or None
        if isinstance(first, str):
            return first.strip() or None
        return None

    @staticmethod
    def _cleanup_client(client: IMAPAdapter) -> None:
        try:
            client.close()
        except (imaplib.IMAP4.error, OSError):
            pass

        try:
            client.logout()
        except (imaplib.IMAP4.error, OSError):
            pass


def _parse_mailbox_name(
    raw_entry: bytes | bytearray | str,
) -> str:
    if isinstance(raw_entry, (bytes, bytearray)):
        text = raw_entry.decode(
            "utf-8",
            errors="ignore",
        )
    else:
        text = raw_entry

    text = text.strip()
    if not text:
        return ""

    quoted_match = re.search(
        r' "((?:[^"\\]|\\.)*)"\s*$',
        text,
    )

    if quoted_match:
        mailbox_name = quoted_match.group(1).replace(
            r"\"",
            '"',
        )
        return _decode_modified_utf7(mailbox_name)

    tail = text.rsplit(
        " ",
        maxsplit=1,
    )[-1]

    if tail.startswith('"') and tail.endswith('"'):
        tail = tail[1:-1]

    return _decode_modified_utf7(tail)


def _decode_modified_utf7(value: str) -> str:
    result: list[str] = []
    index = 0

    while index < len(value):
        character = value[index]

        if character != "&":
            result.append(character)
            index += 1
            continue

        end = value.find("-", index)
        if end == -1:
            result.append(value[index:])
            break

        token = value[index + 1 : end]

        if not token:
            result.append("&")
        else:
            try:
                utf7_bytes = ("+" + token.replace(",", "/") + "-").encode("ascii")

                result.append(utf7_bytes.decode("utf-7"))
            except (UnicodeDecodeError, UnicodeEncodeError):
                result.append(f"&{token}-")

        index = end + 1

    return "".join(result)


def _decode_header_value(value: str) -> str:
    if not value:
        return ""
    try:
        decoded = str(make_header(decode_header(value)))
    except Exception:  # noqa: BLE001
        decoded = value
    return " ".join(decoded.replace("\r", " ").replace("\n", " ").split())


def _parse_uid_list(raw_data: object) -> list[int]:
    if isinstance(raw_data, bytes):
        text = raw_data.decode("utf-8", errors="ignore")
    elif isinstance(raw_data, str):
        text = raw_data
    else:
        return []
    result: list[int] = []
    for chunk in text.split():
        if chunk.isdigit():
            result.append(int(chunk))
    return result
