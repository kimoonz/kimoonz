from pathlib import Path

from paradogo.config import EmailConfig, NotifyConfig, TelegramConfig
from paradogo.notify import Notifier
from paradogo.notify.base import Channel, Message
from paradogo.notify.mail import EmailChannel


def test_message_text_includes_url_when_present():
    msg = Message(title="제목", body="본문", url="https://example.test/pay")
    assert msg.as_text() == "제목\n\n본문\n\n링크: https://example.test/pay"


def test_message_text_without_url():
    assert Message(title="제목", body="본문").as_text() == "제목\n\n본문"


def test_telegram_unusable_without_token():
    assert not TelegramConfig(enabled=True, bot_token="", chat_id="1").usable
    assert not TelegramConfig(enabled=False, bot_token="t", chat_id="1").usable
    assert TelegramConfig(enabled=True, bot_token="t", chat_id="1").usable


def test_email_unusable_without_recipients():
    assert not EmailConfig(enabled=True, smtp_host="h", recipients=[]).usable
    assert EmailConfig(enabled=True, smtp_host="h", recipients=["a@b.c"]).usable


def test_email_message_is_built_with_recipients_and_subject():
    cfg = EmailConfig(
        enabled=True,
        smtp_host="h",
        sender="from@example.test",
        recipients=["a@example.test", "b@example.test"],
    )
    mail = EmailChannel(cfg).build(Message(title="빈자리", body="발견"))
    assert mail["Subject"] == "빈자리"
    assert mail["To"] == "a@example.test, b@example.test"
    assert "발견" in mail.get_content()


def test_email_attaches_screenshot(tmp_path: Path):
    shot = tmp_path / "shot.png"
    shot.write_bytes(b"\x89PNG\r\n")
    cfg = EmailConfig(enabled=True, smtp_host="h", sender="f@x.t", recipients=["a@x.t"])
    mail = EmailChannel(cfg).build(Message(title="t", body="b", screenshot=shot))
    attachments = list(mail.iter_attachments())
    assert len(attachments) == 1
    assert attachments[0].get_filename() == "shot.png"


class ExplodingChannel(Channel):
    name = "boom"

    @property
    def usable(self):
        return True

    def _send(self, message):
        raise RuntimeError("네트워크 끊김")


class RecordingChannel(Channel):
    name = "rec"

    def __init__(self):
        self.received: list[Message] = []

    @property
    def usable(self):
        return True

    def _send(self, message):
        self.received.append(message)


def test_channel_swallows_send_errors():
    # 알림 실패가 예약 실행을 중단시켜서는 안 된다.
    assert ExplodingChannel().send(Message("t", "b")) is False


def test_notifier_keeps_going_when_one_channel_fails():
    notifier = Notifier(NotifyConfig())
    good = RecordingChannel()
    notifier.channels = [ExplodingChannel(), good]
    results = notifier.send("제목", "본문")
    assert results == {"boom": False, "rec": True}
    assert good.received[0].title == "제목"


def test_notifier_reports_no_active_channels_by_default():
    assert Notifier(NotifyConfig()).active == []


def test_notifier_lists_configured_channels():
    cfg = NotifyConfig(
        telegram=TelegramConfig(enabled=True, bot_token="t", chat_id="c"),
        email=EmailConfig(enabled=True, smtp_host="h", recipients=["a@b.c"]),
    )
    assert Notifier(cfg).active == ["telegram", "email"]
