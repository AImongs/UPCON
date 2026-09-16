"""pytest 설정: 실제 클라우드 API 테스트(tests/integration)는 --run-cloud 를 줄 때만 실행."""
import os
import tempfile

import pytest

# 테스트가 실제 사용자 설정/로그(%LOCALAPPDATA%/UPCON)를 건드리지 않도록 격리
os.environ.setdefault("UPCON_DATA_DIR", tempfile.mkdtemp(prefix="upcon_test_data_"))


def pytest_addoption(parser):
    parser.addoption("--run-cloud", action="store_true", default=False, help="실제 fal.ai API 테스트 실행 (비용 발생)")


def pytest_configure(config):
    config.addinivalue_line("markers", "integration: 실제 외부 API 호출 (비용 발생)")


def pytest_collection_modifyitems(config, items):
    if config.getoption("--run-cloud"):
        return
    skip = pytest.mark.skip(reason="--run-cloud 옵션 없음 (실제 API/비용 발생 테스트)")
    for item in items:
        if "integration" in item.keywords:
            item.add_marker(skip)
