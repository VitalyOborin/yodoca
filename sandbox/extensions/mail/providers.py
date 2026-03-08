"""IMAP provider configuration for Gmail and Yandex.Mail."""

from dataclasses import dataclass


@dataclass(frozen=True)
class ProviderConfig:
    """Connection and setup metadata for an IMAP provider."""

    imap_host: str
    imap_port: int
    app_password_url: str
    setup_note: str


PROVIDERS: dict[str, ProviderConfig] = {
    "gmail": ProviderConfig(
        imap_host="imap.gmail.com",
        imap_port=993,
        app_password_url="https://myaccount.google.com/apppasswords",
        setup_note="Requires 2FA enabled on Google account.",
    ),
    "yandex": ProviderConfig(
        imap_host="imap.yandex.ru",
        imap_port=993,
        app_password_url="https://id.yandex.ru/security/app-passwords",
        setup_note="Requires 2FA. Also enable IMAP in Yandex Mail settings.",
    ),
}
