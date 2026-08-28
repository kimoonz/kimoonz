import json
import stat
import sys

import pytest

from paradogo import credentials
from paradogo.credentials import Credentials, backend_name, clear, credentials_path, load, resolve, save


@pytest.fixture(autouse=True)
def no_keyring(monkeypatch):
    """키체인이 없는 환경(파일 대비책)을 기본으로 테스트한다."""
    monkeypatch.setattr(credentials, "_keyring", lambda: None)


def test_save_and_load_roundtrip(tmp_path):
    where = save("myid", "mypw", tmp_path)
    assert "파일" in where
    creds = load(tmp_path)
    assert creds == Credentials("myid", "mypw")


def test_saved_file_is_not_world_readable(tmp_path):
    save("myid", "mypw", tmp_path)
    mode = credentials_path(tmp_path).stat().st_mode
    assert not mode & stat.S_IRGRP
    assert not mode & stat.S_IROTH


@pytest.mark.skipif(sys.platform == "win32", reason="파일 권한 개념이 다름")
def test_saved_file_is_owner_only(tmp_path):
    save("myid", "mypw", tmp_path)
    assert stat.S_IMODE(credentials_path(tmp_path).stat().st_mode) == 0o600


def test_load_returns_none_when_nothing_saved(tmp_path):
    assert load(tmp_path) is None


def test_load_returns_none_on_corrupt_file(tmp_path):
    path = credentials_path(tmp_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{깨진 파일", encoding="utf-8")
    assert load(tmp_path) is None


def test_load_returns_none_when_password_missing(tmp_path):
    path = credentials_path(tmp_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"login_id": "myid", "password": ""}), encoding="utf-8")
    assert load(tmp_path) is None


def test_save_rejects_empty_values(tmp_path):
    with pytest.raises(ValueError):
        save("", "pw", tmp_path)
    with pytest.raises(ValueError):
        save("id", "", tmp_path)


def test_clear_removes_the_file(tmp_path):
    save("myid", "mypw", tmp_path)
    removed = clear(tmp_path)
    assert removed and not credentials_path(tmp_path).exists()
    assert load(tmp_path) is None


def test_clear_on_empty_store_is_harmless(tmp_path):
    assert clear(tmp_path) == []


# --- 설정과 보관소의 우선순위 ------------------------------------------------


def test_config_values_win_over_stored(tmp_path):
    save("stored", "storedpw", tmp_path)
    assert resolve("fromconfig", "configpw", tmp_path) == Credentials("fromconfig", "configpw")


def test_falls_back_to_stored_when_config_is_empty(tmp_path):
    save("stored", "storedpw", tmp_path)
    assert resolve("", "", tmp_path) == Credentials("stored", "storedpw")


def test_config_id_with_stored_password(tmp_path):
    # 아이디는 설정에 적어 두고 비밀번호만 보관소에 두는 경우.
    save("stored", "storedpw", tmp_path)
    assert resolve("otherid", "", tmp_path) == Credentials("otherid", "storedpw")


def test_resolve_returns_unusable_when_nothing_anywhere(tmp_path):
    creds = resolve("", "", tmp_path)
    assert not creds.usable


def test_masked_never_shows_the_password(tmp_path):
    creds = Credentials("kimoonz", "supersecret")
    masked = creds.masked()
    assert "supersecret" not in masked
    assert "kimoonz" not in masked
    assert masked.startswith("ki")


def test_masked_when_empty():
    assert Credentials("", "").masked() == "(없음)"


def test_backend_name_reports_file_without_keyring():
    assert backend_name() == "로컬 파일"


# --- 키체인이 있을 때 --------------------------------------------------------


class FakeKeyring:
    def __init__(self):
        self.store: dict[tuple[str, str], str] = {}

    def set_password(self, service, user, password):
        self.store[(service, user)] = password

    def get_password(self, service, user):
        return self.store.get((service, user))

    def delete_password(self, service, user):
        self.store.pop((service, user), None)


def test_keyring_is_preferred_and_no_file_is_written(tmp_path, monkeypatch):
    fake = FakeKeyring()
    monkeypatch.setattr(credentials, "_keyring", lambda: fake)
    where = save("myid", "mypw", tmp_path)
    assert where == "OS 키체인"
    assert not credentials_path(tmp_path).exists()
    assert load(tmp_path) == Credentials("myid", "mypw")


def test_keyring_clear_removes_both_entries(tmp_path, monkeypatch):
    fake = FakeKeyring()
    monkeypatch.setattr(credentials, "_keyring", lambda: fake)
    save("myid", "mypw", tmp_path)
    clear(tmp_path)
    assert fake.store == {}
    assert load(tmp_path) is None


def test_file_is_still_read_when_keyring_is_empty(tmp_path, monkeypatch):
    # 파일에 저장해 두었다가 나중에 keyring 을 설치한 경우.
    save("myid", "mypw", tmp_path)
    monkeypatch.setattr(credentials, "_keyring", lambda: FakeKeyring())
    assert load(tmp_path) == Credentials("myid", "mypw")
