# UPCON — Cloud 경로(fal.ai FlashVSR) 현황 조사 + 개선 계획

작성일: 2026-09-19 · 배경: [HOTFIX3_4060ti_dn05_corruption_diagnosis.md](HOTFIX3_4060ti_dn05_corruption_diagnosis.md)
(RTX 4060 Ti 등 일부 GPU에서 로컬 커스텀 dn05 모델이 corruption을 일으키는 근본 원인은 특정하지
못했고, 단기 완화 방향을 "로컬이 정상인 PC는 로컬, 문제 있는 PC는 클라우드를 쉽게 쓰게 한다"로
정한 데서 이어지는 조사·계획 문서. **이 문서는 조사와 계획만 담는다. production 코드는 이번
세션에서 수정하지 않았고, 유료 fal.ai API 호출도 하지 않았다.**)

## 요약 (결론부터)

기존에 알고 있던 것보다 클라우드 경로는 이미 상당히 완성도가 높다. 업로드→submit(단발성, 재시도
없음)→폴링→다운로드→검증→저장까지 전체 파이프라인, 실행 전 비용 확인 다이얼로그, keyring 기반
키 저장, 배치 큐 연동, 취소/재시도가 **모두 이미 코드에 있고 단위 테스트로 커버되어 있다**. 사용자가
요청한 14개 목표 대부분은 "미구현"이 아니라 "이미 구현됨"이다.

진짜로 빠진 한 가지는 **HOTFIX-3 진단 문서의 "알려진 gap"과 정확히 같은 지점**이다 — 로컬
self-test가 타일/블록형 corruption을 감지하지 못하므로, Router는 문제 있는 GPU에서도 "로컬 사용
가능"으로 판단해 클라우드로 자동 전환하지 않는다. 즉 사용자가 요청한 목표 1번("로컬 호환성 문제가
있는 PC는 클라우드를 쉽게 쓰게 한다")의 **자동 판별 부분만 아직 없다** — 수동으로 '클라우드 GPU'를
선택하는 것은 이미 가능하다.

---

## 1. 이미 구현된 것 (실제 코드 기준)

| 영역 | 구현 위치 | 내용 |
|---|---|---|
| Provider | `upcon/providers/fal_flashvsr.py` | 분석→예상비용→업로드(CDN, 1일 만료)→submit→폴링(대기열 순번/경과)→다운로드(바이트 진행률)→ffprobe 검증→오디오 없으면 로컬 mux→저장→실제 청구액 조회(best-effort) |
| 과금 지점 보호 | `fal_base.FalApi.submit_once()` | fal_client SDK의 자동 재시도(최대 10회)를 우회해 추론 요청(submit)만 정확히 1회 POST — 이중 과금 방지. 업로드/상태조회/결과조회는 그대로 재시도 허용 |
| 예상 비용 | `upcon/core/pricing.py` | 출력 해상도×프레임 기준 MP × 단가. API 조회 단가(`AppConfig.cloud_unit_prices`, 연결 테스트 시 캐시) 우선, 없으면 문서 상수(2026-09-15 확인, $0.0005/MP). 표시는 센트 단위 올림(보수적), 최소 $0.01. KRW 환산은 `krw_per_usd`(기본 0=미표시, 하드코딩 금지) |
| 실행 전 확인 UI | `upcon/app/main_window.py:_confirm_cloud()` | 클라우드로 처리 전 파일 수·총 예상비용(KRW 병기 가능)·"내 fal.ai 계정에서 청구됨" 문구가 담긴 확인 다이얼로그. '계속' 눌러야 `start_all()` 호출 → **명시적 실행 없이는 과금 없음** |
| API Key 저장 | `upcon/core/credentials.py` | Windows Credential Manager(keyring, 서비스명 `UPCON`, 사용자명 `fal_api_key`). PyInstaller 배포본에서 자동 백엔드 탐색이 실패할 수 있어 OS 표준 백엔드로 명시 고정. 저장 직후 read-back 검증. 평문 config.json/로그에는 절대 저장 안 함(로그는 앞 4자만 마스킹) |
| 설정 UI | `upcon/app/cloud_settings.py` | 키 입력(마스킹/표시 토글)·연결 테스트(백그라운드 스레드)·단가 표시·삭제. 저장은 keyring, `AppConfig.cloud_provider`만 설정 파일에 남음 |
| 처리 방식 전환 | `upcon/app/widgets/options_panel.py` | "자동/내 PC/클라우드" 라디오 버튼 한 줄 — 클릭 한 번으로 전환. 각 모드 설명 문구(hint) 실시간 갱신 |
| Router | `upcon/core/router.py` | AUTO 모드에서 로컬 불가 시 `RoutingConfig.allow_cloud_fallback`(기본 true)이면 클라우드로 자동 전환. CLOUD 모드 강제 선택도 지원 |
| JobManager(배치) | `upcon/core/jobs.py` | provider-agnostic — 로컬/클라우드 Job을 동일하게 순차 처리. 실패해도 배치 전체가 죽지 않고 다음 파일 진행 |
| 재시도 | `JobManager.retry_failed()` | FAILED/CANCELLED/INTERRUPTED → PENDING으로 리셋하되 `provider_id`(어느 provider였는지)는 보존 → 재시작 시 같은 방식(클라우드면 클라우드)으로 재시도. 배치를 다시 시작(▶)할 때 `_on_start_all()`이 비용을 다시 계산해 확인 다이얼로그를 다시 띄움(재시도도 매번 재확인) |
| 취소 | `fal_flashvsr._try_cancel()` | 대기열 상태에서 취소 → "비용 없음" 안내. 처리 중 상태에서 취소 → "이미 처리 중이던 요청은 취소가 반영 안 될 수 있고 비용이 청구될 수 있음" 안내. fal `handle.cancel()` 실제 호출 |
| 결과 저장 | 동일 파일 `_verify_and_fix_audio`, `_download` | 다운로드는 바이트 단위 진행률 콜백, ffprobe로 해상도/길이 검증(경고 로그), 오디오 유실 시 로컬 ffmpeg mux, 완료 후 `output.parent`에 원자적 이동. 실패 시 부분 생성된 출력 파일 삭제 |
| 오류 처리 | `upcon/providers/fal_base.py:explain_fal_error()` | HTTP 401/402/403/404/422/429/5xx, 타임아웃, 네트워크 예외를 전부 한국어 사용자 메시지로 매핑(키 오류/잔액 부족/모델 없음/영상 제한/속도 제한/서버 오류 구분). `detail`(원문)은 로그 전용, 화면에 노출 안 함 |
| ADMIN 키 불요구 | `fal_base.py` 전체 | 인증은 일반 `Authorization: Key <FAL_KEY>` 한 종류만 사용. 유일하게 ADMIN 스코프가 필요한 `billing-events`(실제 청구액 조회)는 **실패해도 조용히 None으로 폴백**하고 "예상 비용 …(실제 청구액은 대시보드에서 확인)"으로 대체 — 일반 키만으로 전체 기능이 동작 |
| 테스트 | `tests/test_cloud.py`(단위, 실제 API 호출 없음, httpx/keyring mock), `tests/integration/test_fal_flashvsr.py`(실과금, `-m integration --run-cloud`로만 실행, 키 없으면 자동 skip) | 가격 계산, 라우팅, 오류 매핑, 취소, 배치 연동을 커버 |

## 2. 미구현/불완전한 것

1. **(가장 중요) 로컬 corruption 자동 감지 → 클라우드 자동 전환 없음.** `LocalNcnnProvider._self_test()`는
   단색 64×64 이미지로 "모델 로드+실행 성공 여부"만 확인한다. HOTFIX-3에서 확인된 타일/블록형
   corruption은 균일 색상 이미지에서 드러나지 않는 결함이라 self-test가 절대 잡지 못한다. 결과: 4060 Ti
   같은 영향받는 GPU에서도 Router는 "로컬 사용 가능"이라고 판단 → AUTO 모드가 클라우드로 넘어가지
   않는다. 사용자가 수동으로 '클라우드 GPU'를 선택해야만 우회 가능. **이번에 요청한 목표(문제 있는 PC는
   클라우드를 쉽게 쓰게)의 핵심 자동화 부분이 비어 있다.**
2. Cloud는 `output_mode == 2×`만 지원(`FalFlashVSRProvider.check_availability`가 명시적으로
   1080p/4K를 거부). FlashVSR API 자체가 목표 해상도 개념이 없고 배율만 받기 때문 — API 계약을
   임의로 바꾸지 않는다는 기존 설계 결정에 따른 의도된 제약이지만, 1080p/4K를 원하는 사용자는
   "로컬 불가 → 클라우드도 안 됨"으로 막힐 수 있다.
3. `AppConfig.cloud_extra_args`(예: `{"acceleration": "high"}`) 필드는 있지만 이를 편집할 Settings UI가
   없다 — 지금은 config.json을 직접 고쳐야 한다.
4. 배치 하나에는 provider를 하나만 지정한다(`Controller.start_all()`이 PENDING 전체에 동일 provider를
   일괄 적용). 같은 배치 안에서 파일별로 로컬/클라우드를 섞어 실행할 수는 없다. (다른 PC끼리 다른 걸
   쓰는 이번 목표에는 문제 없지만, 한 PC에서 "이 파일만 클라우드로"는 아직 안 됨.)
5. 업로드/폴링/다운로드 도중 네트워크 예외가 나면 그 즉시 Job이 FAILED로 끝난다(그 안에서 자동
   재시도 없음) — 재시도는 사용자가 큐에서 수동으로 눌러야 한다. 이미 요청한 "실패 시 재시도 가능"은
   충족하지만, 일시적 네트워크 끊김에도 배치가 중단 없이 계속 진행되길 원한다면 자동 재시도(지수
   백오프 등)는 없다.
6. 실제 청구액(`actual_cost_usd`)은 최선 노력으로 최대 3회(4초)만 재시도하고 못 가져오면 포기한다 —
   fal 쪽 집계 지연이나 ADMIN 스코프 부재 시 사용자는 "예상 비용"만 보고, 실제 청구액 확인은
   fal.ai 대시보드로 안내만 한다(요구사항 13번 "혼동 방지"에는 부합하지만, 정확한 실청구 자동 표시는
   구조적으로 불가능 — ADMIN 키를 요구하지 않기로 한 제약과 트레이드오프).

## 3. FlashVSR 현재 API 연동 구조

- 엔드포인트: `fal-ai/flashvsr/upscale/video` (Queue REST, `fal_client` SDK 경유)
- 인자: `video_url`(CDN 업로드 후 URL), `upscale_factor`(=scale, 항상 2), `preserve_audio=True`,
  `output_format="X264 (.mp4)"` + `AppConfig.cloud_extra_args`로 덮어쓰기 가능
- 업로드: `client.upload_file(..., lifecycle=StorageSettings(expires_in="1d"))` — 원본은 CDN에서 하루 뒤 자동 삭제
- 결과 보존 힌트 헤더: `X-Fal-Object-Lifecycle-Preference: 7일`
- submit: SDK 재시도 우회한 단발 POST(`FalApi.submit_once`) — 이중 과금 방지가 목적
- 폴링: `handle.status()`로 `Queued`(대기열 순번)/`InProgress`(경과 시간만, 실제 %없음)/`Completed`
  구분, 2초 간격, 최대 3시간 타임아웃
- 결과: `result["video"]["url"]` + `file_size` 힌트 → 스트리밍 다운로드(바이트 진행률)

## 4. API Key 저장 구조

- `keyring` 라이브러리, 서비스명 `APP_NAME`("UPCON"), 계정명 `"fal_api_key"`
- Windows: Credential Manager로 백엔드 강제 고정(자동 탐색 실패 시에만 개입) — PyInstaller
  배포본에서 entry-point 자동 탐색이 실패해 `NoKeyringError`가 나는 걸 막기 위함
- 저장 시 길이 제한(UTF-16LE 2560바이트, CredWrite 일반 blob 한도) 사전 검사, 저장 직후 read-back
  검증 실패 시 `CredentialStoreError`(사용자 메시지 포함)
- 개발 편의용 `FAL_KEY` 환경변수 폴백(순위상 keyring보다 낮음) — 사용자에게는 노출되지 않음
- 로그/화면에는 키를 절대 쓰지 않고 `mask()`로 앞 4자만 노출

## 5. 예상 비용 계산 구조

- 기준: **출력**(업스케일 후) 해상도 × 프레임 수 ÷ 1,000,000 × 단가(USD/MP) — fal 공식 예시
  (1920×1080, 121프레임 → $0.125)와 일치 검증됨(`tests/test_cloud.py::test_pricing_matches_official_example`)
- 단가 소스: ① 연결 테스트 시 Platform API(`GET /v1/models/pricing`)로 조회해 `AppConfig.cloud_unit_prices`에
  캐시 → ② 실패 시 문서 상수(`pricing.DOCUMENTED_UNIT_PRICES_USD_PER_MP`, 2026-09-15 확인)
- 표시값은 센트 단위 올림(`math.ceil`) + 최소 $0.01 — 항상 보수적으로(실비용 ≤ 표시값 방향으로) 반올림
- KRW 환산은 `krw_per_usd`(기본 0=비표시)로만 존재, 환율은 **어디에도 하드코딩되어 있지 않음**(STEP1
  설계 결정 준수) — 실제 환율 조회 기능은 아직 없음(기획만 있고 미구현)

## 6. batch와 cloud 연동 상태

`JobManager`는 provider를 몰라도 되게 설계돼 있어(=`JobRunner` 콜백 하나만 받음) 로컬/클라우드
Job이 완전히 동일하게 취급된다. `Controller.start_all()`이 배치 시작 시점에 PENDING 전체에 같은
provider를 일괄 지정하고, `total_cloud_cost()`로 배치 전체의 예상 비용 합계를 계산해 실행 전
확인창에 표시한다. 이미 완료(DONE)된 파일은 재확인 없이 건너뛴다. → **연동 자체는 완성**돼 있고,
빠진 건 "같은 배치 안에서 파일별 provider 혼합"뿐(§2-4).

## 7. cancel/retry 상태

- Cancel: `CancelToken`(threading.Event 기반)이 Job 전체에 공유돼, 업로드 중/대기열 중/처리 중 어느
  단계에서 취소해도 다음 체크포인트에서 `CancelledError`로 빠져나옴. fal 쪽 `handle.cancel()`도 실제
  호출하고, 그 시점(대기열 vs 처리 중)에 따라 "비용 없음" 또는 "비용 청구될 수 있음" 안내를 `job.note`에
  남김. 통합 테스트(`test_cancel_during_queue`)로 실제 취소 흐름 검증됨.
- Retry: `JobManager.retry_failed()`가 FAILED/CANCELLED/INTERRUPTED → PENDING, `provider_id`는 보존.
  배치를 다시 ▶ 누르면 비용을 다시 계산해 확인창을 다시 띄움(재시도도 매번 재확인 — 혼동 방지
  요구사항과 부합).

## 8. cloud 결과 다운로드/저장 상태

`httpx` 스트리밍 다운로드(1MB 청크, 0.2초 간격으로 진행률 콜백), `content-length` 또는 API가 준
`file_size` 힌트로 퍼센트 표시. 완료 후 ffprobe로 해상도(±32px 허용)/길이(±5%, 최소 1초 허용) 검증
(불일치 시 경고 로그만, 실패 처리는 안 함 — 클라우드 쪽 리사이즈 오차 허용). 오디오가 있었는데
결과에 없으면 로컬 ffmpeg mux로 복구. 최종적으로 임시 파일을 `shutil.move`로 출력 폴더에 원자적
이동, 이름 충돌 시 `_2x_2.mp4`식 번호 부여(로컬과 동일 로직).

## 9. 오류 처리 상태

`fal_base.explain_fal_error()`가 모든 예외(HTTP 상태코드별/타임아웃/네트워크)를 한국어
`UpconError`로 변환하고, `JobManager._run()`이 `UpconError`와 예상 못한 `Exception`을 분리 처리해
한 파일의 실패가 배치 전체를 죽이지 않는다. `detail`(원문/스택 요약)은 로그에만, `user_message`만
화면에 노출 — 요구사항 13번(예상 vs 실제 과금 문구 설계)과 별개로 이미 "내부 오류 정보 비노출"
원칙은 지켜지고 있다.

## 10. 개선 구현 순서 (권장)

1. **로컬 corruption 감지 self-test 강화** — 균일 색상이 아닌 고주파(텍스처/에지가 있는) 테스트
   패턴으로 실제 추론을 돌려 통계적 이상(타일 경계 불연속, 색상 히스토그램 이상 등)을 감지하는
   self-test로 교체. 감지되면 Router가 해당 로컬 provider를 "사용 불가"로 보고 → AUTO 모드가 자동으로
   클라우드로 전환. **이번 요청의 목표 1번을 실제로 완성시키는 유일한 항목이라 최우선.**
2. Router/Availability 문구에 "로컬 GPU 호환성 문제가 감지되어 클라우드로 전환합니다" 같은 명확한
   사용자 안내 추가(그냥 "사용 가능한 GPU가 없어…"로 뭉뚱그리지 않기 — 사용자가 왜 클라우드로
   넘어갔는지 알아야 함).
3. Cloud Settings UI에 `cloud_extra_args`(acceleration 등) 편집 UI 추가(선택 사항, 우선순위 낮음).
4. 업로드/폴링/다운로드의 일시적 네트워크 오류에 대한 제한적 자동 재시도(지수 백오프, 소수 회) 검토
   — 단, submit(과금 지점)은 절대 자동 재시도하지 않는 현재 원칙 유지.
5. (장기) 배치 내 파일별 provider 혼합 지원 — 현재 요구사항 범위 밖이라 우선순위 낮음.

## 11. 실제 과금 없이 구현/테스트 가능한 범위

거의 전부. `tests/test_cloud.py` 패턴처럼 `httpx`/`fal_client`를 mock하면:
- self-test 강화(§10-1)는 로컬 GPU만 있으면 되고 fal.ai와 무관 — 전부 무료로 개발/검증 가능.
- Router 문구 변경(§10-2), Settings UI 확장(§10-3)은 UI 테스트로 검증.
- 재시도 백오프(§10-4)는 mock으로 실패를 주입해 재시도 횟수/지연을 검증 가능.
- `FalApi.test_connection()`(키 검증 + 단가 조회)은 **추론이 아니라 과금되지 않는 호출**이지만,
  실제 키가 있어야 실행되므로 지금(키 없음) 단계에서는 mock으로만 검증.

## 12. 실제 유료 검증이 필요한 최소 테스트

개발이 끝난 뒤, 딱 한 번:
1. `test_connection()` — 과금 없음, 키 유효성/단가 조회만 확인.
2. `tests/integration/test_fal_flashvsr.py`의 480p·10초 안팎 최소 클립 1건 종단 테스트
   (`test_a_480p_10s_with_audio` 수준) — 업로드→submit→폴링→다운로드→오디오 검증까지 전체 경로가
   실제로 동작하는지, fal 대시보드에 청구가 정상적으로 찍히는지 확인.
3. `test_cancel_during_queue` 1건 — 대기열 취소가 실제로 비용 없이 끝나는지 확인.

이 세 가지 외에는 위(§11)에서 전부 mock으로 검증 가능하므로, 실제 과금은 최소 1~2건(수 센트
수준)으로 충분하다. **이번 세션에서는 키가 없는 상태를 그대로 두고 어떤 유료 호출도 실행하지
않았다.**

## 13. git status (조사 시점)

```
브랜치: master (HEAD == origin 추적 여부는 별도 확인 안 함)
 M packaging/build_installer.py
 M packaging/upcon.iss
 M pyproject.toml
 M tests/test_pipeline.py
 M upcon/core/ffmpeg.py
 M upcon/providers/local_ncnn.py
 M upcon/version.py
?? docs/HOTFIX3_4060ti_dn05_corruption_diagnosis.md
?? packaging/diag/
```

이 변경분(버전 0.3.0→0.3.2, HOTFIX-2 진단용 프레임 저장 계측, 인코더 검증/폴백 등)은 이번
cloud 조사와 무관한 기존 작업 중인 내용이며, **이번 세션에서 건드리지 않았다**.

## 14. HEAD

```
0682711ab832061e231d30629e8aa303c535b0d — Fix macOS arm64 GitHub Actions runner
```
