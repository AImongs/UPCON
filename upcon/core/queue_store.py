"""대기열 저장/복원 (%LOCALAPPDATA%/UPCON/queue.json).

- 앱이 비정상 종료되면 '처리 중' 이던 항목은 '중단됨' 으로 복원한다 (자동 이어붙이기 없음).
- 완료 항목은 복원하지 않는다. 영상 정보는 다시 분석한다 (파일이 바뀌었을 수 있음).
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

from upcon.core.constants import parse_output_mode
from upcon.core.jobs import Job, JobStatus
from upcon.core.paths import user_data_dir

log = logging.getLogger(__name__)
RESTORE_STATUSES = {JobStatus.PENDING, JobStatus.FAILED, JobStatus.CANCELLED, JobStatus.INTERRUPTED}


def queue_file() -> Path:
    return user_data_dir() / "queue.json"


def save_queue(jobs: list[Job], path: Path | None = None) -> None:
    path = path or queue_file()
    data = [{
        "id": j.id, "input_path": str(j.input_path), "scale": j.scale,
        "output_mode": j.output_mode.value, "status": j.status.value,
        "output_path": str(j.output_path) if j.output_path else "", "error_message": j.error_message,
        "provider_id": j.provider_id,
    } for j in jobs]
    try:
        tmp = path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
        tmp.replace(path)
    except OSError as e:
        log.warning("queue save failed: %s", e)


def load_queue(path: Path | None = None) -> list[Job]:
    path = path or queue_file()
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as e:
        log.warning("queue load failed: %s", e)
        return []
    jobs: list[Job] = []
    for d in data:
        try:
            status = JobStatus(d.get("status", "pending"))
        except ValueError:
            status = JobStatus.PENDING
        if status == JobStatus.RUNNING:
            status = JobStatus.INTERRUPTED           # 비정상 종료 → 중단됨
        if status not in RESTORE_STATUSES:
            continue
        p = Path(d.get("input_path", ""))
        if not p.exists():
            log.info("queue restore: missing file skipped (%s)", p.name)
            continue
        # output_mode 가 없는 기존 UPCON 0.3.0 대기열이거나 값이 잘못돼도 항상 2× 로 안전하게 복원한다.
        job = Job(input_path=p, scale=int(d.get("scale", 2)), output_mode=parse_output_mode(d.get("output_mode", "")),
                  status=status, error_message=d.get("error_message", "") if status == JobStatus.FAILED else "",
                  provider_id=d.get("provider_id", ""))
        if status == JobStatus.INTERRUPTED:
            job.error_message = "이전 실행이 비정상 종료되어 중단되었습니다. '중단된 항목 다시 시작' 으로 다시 처리할 수 있습니다."
        jobs.append(job)
    if jobs:
        log.info("queue restored: %d items", len(jobs))
    return jobs


def clear_queue_file(path: Path | None = None) -> None:
    path = path or queue_file()
    try:
        path.unlink(missing_ok=True)
    except OSError:
        pass
