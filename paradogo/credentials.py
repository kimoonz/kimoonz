"""로그인 정보 보관.

상시 감시를 돌리면 세션은 언젠가 만료된다. 새벽 3시에 풀렸는데 사람이 다시
로그인해 줘야 한다면 그날 아침까지는 감시가 멈춘 것이나 마찬가지다. 그래서 아이디와
비밀번호를 저장해 두고 자동으로 다시 로그인할 수 있게 한다.

보관 위치는 두 가지다.

* **OS 키체인** (Windows 자격 증명 관리자 / macOS 키체인 / Linux Secret Service).
  ``keyring`` 패키지가 설치돼 있으면 이쪽을 쓴다. 다른 프로그램이 함부로 읽지 못한다.
* **로컬 파일** (``.state/credentials.json``, 권한 600). 키체인을 못 쓸 때의 대비책.
  **이건 암호화가 아니다.** 그 PC 를 쓸 수 있는 사람은 읽을 수 있다. 대신 저장소에
  올라가지 않도록 ``.state/`` 는 통째로 git 에서 제외돼 있다.

설정 YAML 에는 절대 비밀번호를 적지 않는다. 실수로 커밋되면 그대로 공개된다.
"""

from __future__ import annotations

import json
import logging
import os
import stat
from dataclasses import dataclass
from pathlib import Path

log = logging.getLogger(__name__)

SERVICE_NAME = "paradogo"
KEYRING_USER_FIELD = "__login_id__"


@dataclass(frozen=True, slots=True)
class Credentials:
    login_id: str
    password: str

    @property
    def usable(self) -> bool:
        return bool(self.login_id and self.password)

    def masked(self) -> str:
        """로그에 남겨도 되는 형태."""
        if not self.login_id:
            return "(없음)"
        head = self.login_id[:2]
        return f"{head}{'*' * max(1, len(self.login_id) - 2)} / 비밀번호 {len(self.password)}자"


def _keyring():
    """keyring 을 쓸 수 있으면 모듈을, 아니면 None."""
    try:
        import keyring
        from keyring.errors import NoKeyringError
    except ImportError:
        return None
    try:
        # 백엔드가 실제로 동작하는지 확인한다. 설치만 되고 못 쓰는 환경이 흔하다.
        backend = keyring.get_keyring()
        if backend is None or "fail" in type(backend).__name__.lower():
            return None
        keyring.get_password(SERVICE_NAME, KEYRING_USER_FIELD)
    except NoKeyringError:
        return None
    except Exception as exc:
        log.debug("키체인을 쓸 수 없습니다: %s", exc)
        return None
    return keyring


def backend_name() -> str:
    return "OS 키체인" if _keyring() is not None else "로컬 파일"


def credentials_path(state_dir: Path) -> Path:
    return state_dir / "credentials.json"


def save(login_id: str, password: str, state_dir: Path) -> str:
    """저장하고 어디에 저장했는지 돌려준다."""
    if not login_id or not password:
        raise ValueError("아이디와 비밀번호가 모두 필요합니다.")

    ring = _keyring()
    if ring is not None:
        ring.set_password(SERVICE_NAME, KEYRING_USER_FIELD, login_id)
        ring.set_password(SERVICE_NAME, login_id, password)
        return "OS 키체인"

    path = credentials_path(state_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps({"login_id": login_id, "password": password}, ensure_ascii=False),
        encoding="utf-8",
    )
    try:
        path.chmod(stat.S_IRUSR | stat.S_IWUSR)  # 본인만 읽기/쓰기
    except OSError as exc:
        log.warning("파일 권한을 조이지 못했습니다: %s", exc)
    return f"파일 {path}"


def load(state_dir: Path) -> Credentials | None:
    """저장된 로그인 정보. 없으면 None."""
    ring = _keyring()
    if ring is not None:
        try:
            login_id = ring.get_password(SERVICE_NAME, KEYRING_USER_FIELD)
            if login_id:
                password = ring.get_password(SERVICE_NAME, login_id)
                if password:
                    return Credentials(login_id, password)
        except Exception as exc:
            log.debug("키체인 읽기 실패: %s", exc)

    path = credentials_path(state_dir)
    if not path.exists():
        return None
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        log.warning("저장된 로그인 정보를 읽지 못했습니다: %s", exc)
        return None
    creds = Credentials(str(raw.get("login_id") or ""), str(raw.get("password") or ""))
    return creds if creds.usable else None


def clear(state_dir: Path) -> list[str]:
    """저장된 것을 지우고, 지운 곳을 알려준다."""
    removed: list[str] = []
    ring = _keyring()
    if ring is not None:
        try:
            login_id = ring.get_password(SERVICE_NAME, KEYRING_USER_FIELD)
            if login_id:
                ring.delete_password(SERVICE_NAME, login_id)
                ring.delete_password(SERVICE_NAME, KEYRING_USER_FIELD)
                removed.append("OS 키체인")
        except Exception as exc:
            log.debug("키체인 삭제 실패: %s", exc)

    path = credentials_path(state_dir)
    if path.exists():
        path.unlink()
        removed.append(str(path))
    return removed


def resolve(config_id: str, config_password: str, state_dir: Path) -> Credentials:
    """실제로 로그인에 쓸 값.

    설정(환경변수 치환 포함)에 있으면 그걸 쓰고, 없으면 저장해 둔 것을 꺼낸다.
    """
    if config_id and config_password:
        return Credentials(config_id, config_password)
    stored = load(state_dir)
    if stored is not None:
        # 설정에 아이디만 적어 두고 비밀번호는 보관소에 두는 경우도 받아준다.
        if config_id and config_id != stored.login_id:
            return Credentials(config_id, stored.password)
        return stored
    return Credentials(config_id, config_password)


def env_hint() -> str:
    """환경변수로 넣은 경우를 위한 안내."""
    if os.environ.get("PARADOGO_PW"):
        return "환경변수 PARADOGO_PW 가 설정돼 있어 그 값이 우선 쓰입니다."
    return ""
