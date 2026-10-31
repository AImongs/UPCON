"""수강생 확인(upcon.core.license) — 시트 웹 앱은 가짜 응답으로만 시험한다(네트워크·실제 자격 증명 없음)."""

from __future__ import annotations

import json
import time

import pytest

from upcon.core import license as lic


class FakeResp:
    def __init__(self, data, status=200):
        self._data, self.status_code = data, status

    def json(self):
        if isinstance(self._data, Exception):
            raise self._data
        return self._data


class FakeSheet:
    """강사 시트 흉내: 명단 + PC 기록(최대 대수)."""

    def __init__(self, students=None, max_pcs=2):
        self.students = students or {("홍길동", "01012345678")}
        self.max = max_pcs
        self.pcs: dict[str, set[str]] = {}
        self.calls: list[dict] = []
        self.offline = False

    def post(self, url, content, headers, timeout, follow_redirects):
        if self.offline:
            raise OSError("network down")
        req = json.loads(content.decode("utf-8"))
        self.calls.append(req)
        if (req["name"].replace(" ", ""), req["phone"]) not in self.students:
            return FakeResp({"ok": False, "reason": "not_student"})
        used = self.pcs.setdefault(req["phone"], set())
        if req["action"] == "release":
            used.discard(req["pc"])
            return FakeResp({"ok": True, "used": len(used), "max": self.max})
        if req["pc"] in used:
            return FakeResp({"ok": True, "used": len(used), "max": self.max})
        if req["action"] == "check":
            return FakeResp({"ok": False, "reason": "pc_removed"})
        if len(used) >= self.max:
            return FakeResp({"ok": False, "reason": "limit", "used": len(used), "max": self.max})
        used.add(req["pc"])
        return FakeResp({"ok": True, "used": len(used), "max": self.max})


class MemStore:
    def __init__(self):
        self.v = None

    def get(self):
        return self.v

    def set(self, v):
        self.v = v

    def delete(self):
        self.v = None


def mgr(tmp_path, sheet, pc="AAAAA-11111", store=None):
    return lic.LicenseManager(tmp_path, store or MemStore(), post=sheet.post, url="https://example.invalid/x", pc_code=pc)


def test_not_allowed_before_activation(tmp_path):
    assert not mgr(tmp_path, FakeSheet()).allowed()


def test_activate_student_and_persist(tmp_path):
    sheet = FakeSheet()
    store = MemStore()
    m = mgr(tmp_path, sheet, store=store)
    st = m.activate("홍 길동", "010-1234-5678")
    assert m.allowed() and st.holder == "홍 길동" and st.phone_hint == "5678"
    assert sheet.calls[-1]["action"] == "activate" and sheet.calls[-1]["app_version"].startswith("UPCON ")
    # 다시 켜면(새 manager) 파일 상태로 통과, 이름·전화번호는 자격 증명 저장소에만
    m2 = mgr(tmp_path, sheet, store=store)
    assert m2.allowed()
    assert "01012345678" not in (tmp_path / lic.STATE_FILE).read_text(encoding="utf-8")
    assert json.loads(store.v)["phone"] == "01012345678"


def test_not_student(tmp_path):
    with pytest.raises(lic.LicenseError, match="명단에 없습니다"):
        mgr(tmp_path, FakeSheet()).activate("김철수", "010-9999-9999")


def test_pc_limit(tmp_path):
    sheet = FakeSheet(max_pcs=2)
    mgr(tmp_path / "a", sheet, pc="AAAAA-00001").activate("홍길동", "01012345678")
    mgr(tmp_path / "b", sheet, pc="AAAAA-00002").activate("홍길동", "01012345678")
    with pytest.raises(lic.LicenseError) as e:
        mgr(tmp_path / "c", sheet, pc="AAAAA-00003").activate("홍길동", "01012345678")
    assert e.value.limit_reached and "2대" in str(e.value)


@pytest.fixture(autouse=True)
def _dirs(tmp_path):
    for d in ("a", "b", "c"):
        (tmp_path / d).mkdir()


def test_refresh_removed_from_roster_blocks(tmp_path):
    sheet = FakeSheet()
    m = mgr(tmp_path, sheet)
    m.activate("홍길동", "01012345678")
    sheet.students.clear()                                   # 강사가 명단에서 지움
    m.refresh()
    assert not m.allowed() and "명단에 없습니다" in m.state.message


def test_refresh_offline_keeps_grace(tmp_path):
    sheet = FakeSheet()
    m = mgr(tmp_path, sheet)
    m.activate("홍길동", "01012345678")
    sheet.offline = True
    m.refresh()
    assert m.allowed()
    # 마지막 확인이 유예 기간을 넘기면 막힌다
    assert not lic.is_active(m.state, now=time.time() + (lic.GRACE_DAYS + 1) * 86400)


def test_release_frees_pc(tmp_path):
    sheet = FakeSheet(max_pcs=1)
    m = mgr(tmp_path / "a", sheet, pc="AAAAA-00001")
    m.activate("홍길동", "01012345678")
    m.release()
    assert not m.allowed() and m.store.get() is None
    mgr(tmp_path / "b", sheet, pc="AAAAA-00002").activate("홍길동", "01012345678")   # 자리가 비었으므로 성공


def test_bad_server_response(tmp_path):
    def post(*a, **k):
        return FakeResp(ValueError("html"), status=500)
    m = lic.LicenseManager(tmp_path, MemStore(), post=post, url="https://example.invalid/x", pc_code="AAAAA-11111")
    with pytest.raises(lic.LicenseError, match="HTTP 500"):
        m.activate("홍길동", "01012345678")


@pytest.mark.parametrize("raw,expected", [
    ("010-1234-5678", "01012345678"),
    ("+82 10 1234 5678", "01012345678"),
    ("홍길동\t010-1234-5678\t2", "01012345678"),
])
def test_normalize_phone(raw, expected):
    assert lic.normalize_phone(raw) == expected


def test_machine_code_matches_dramacon_format():
    # DramaCon 과 같은 해시 → 같은 PC 면 같은 코드(시트에서 1대로 센다)
    code = lic.machine_code("same-guid")
    assert code == lic.machine_code("same-guid") and len(code) == 11 and code[5] == "-"
    import base64
    import hashlib
    d = hashlib.sha256(b"dramacon-pc:same-guid").digest()
    s = base64.b32encode(d).decode()[:10]
    assert code == f"{s[:5]}-{s[5:]}"
