from __future__ import annotations

import imaplib
import logging
import re
import ssl
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


class IMAPAdapter(Protocol):
    def login(self, username: str, password: str): ...

    def list(self, directory: str = "", pattern: str = "*"): ...

    def select(self, mailbox: str): ...

    def uid(self, command: str, *args): ...

    def close(self): ...

    def logout(self): ...


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

    def fetch_linkedin_messages(self) -> list[RawEmailMessage]:
        client = self._open_authenticated_client()

        try:
            status, _ = client.select(f'"{self._folder}"')
            if status != "OK":
                raise EmailConnectionError(
                    f"Cannot select mailbox folder: {self._folder}"
                )

            since_date = (
                datetime.now(timezone.utc) - timedelta(days=self._search_days)
            ).strftime("%d-%b-%Y")

            status, data = client.uid(
                "search",
                None,
                f'(SINCE "{since_date}")',
            )

            if status != "OK" or not data:
                return []

            uids = [uid.decode("utf-8") for uid in data[0].split() if uid]

            messages: list[RawEmailMessage] = []

            for uid in uids:
                try:
                    raw_message = self._fetch_message_by_uid(
                        client=client,
                        uid=uid,
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
                        uid,
                        "+FLAGS",
                        "(\\Seen)",
                    )

            return messages

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
