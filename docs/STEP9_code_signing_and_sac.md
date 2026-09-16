# STEP 9 — Windows 코드 서명 조사 결과 및 Smart App Control 사전 검증

조사·검증 기준일: **2026-09-16**
기준 커밋: `a58b0f5 Polish UPCON installer branding`

> 이 문서는 **조사·계획 기록**이다. 이 STEP에서 인증서 구매·결제·실제 코드 서명·
> 배포 파일 수정은 수행하지 않았다.
> 가격과 정책은 변경될 수 있으므로 실제 진행 시 각 절의 출처를 재확인할 것.

---

## 0-1. 현재 상태 요약 및 진행 조건

### 확정된 상태

| 항목 | 상태 |
|---|---|
| 개발 PC Smart App Control | **Off** (실측. §2) |
| 최종 서명 범위 | **미확정** — SAC 실측 후 결정 (§0, §5) |
| Microsoft 지침 | **앱의 모든 application binary 서명 권장** (exe, dll, temp installer files, scripts, uninstallers) |
| 서명 알고리즘 전제 | **RSA** — SAC는 ECC 미지원 (§1) |
| self-signed 인증서 | SAC / Public Trust **검증용으로 사용 불가.** 파이프라인 기술 리허설 전용 (§1) |
| Real-ESRGAN 가중치 라이선스 | **저자 답변 대기 중** (문의 메일은 2026-09-16 사용자가 직접 발송 완료. **추가 발송하지 않는다**) |

### 진행 조건 (순서 고정)

```
[현재] 코드 서명 조사 + SAC 계획 확정          ← 이 문서
   ↓   (대기) Real-ESRGAN 저자 답변 + 실제 배포 여부 확정
   ↓
[1] 별도 Win11 SAC 테스트 환경 구축 (§7)
[2] 미서명 현행 빌드로 SAC 실측 → 서명 범위 A/B 확정
   ↓
[3] Public Trust 인증서 발급 (RSA)
[4] 서명 파이프라인 구축 및 서명
```

**[2]는 [3]보다 반드시 앞선다.** SAC 실측 결과가 필요 서명 대상 수
(A만 3~4개 vs A+B 17~18개)를 결정하며, 이는 인증서/서명 서비스 선택에 직접 영향을 준다.
실측은 인증서 없이 수행 가능하다(§7-5).

### 금지 사항 (결정 사항)

- ❌ **개발 PC의 SAC 상태를 강제로 활성화하지 않는다.** 레지스트리, EFI 시스템 파티션,
  Code Integrity 정책 변경 금지. **Windows 보안 UI를 통한 On/Evaluation 전환 시도도
  하지 않는다.** SAC 동작 확인은 오직 별도 테스트 환경에서만 한다.
- ❌ 테스트 VM은 **지금 만들지 않는다.** 위 진행 조건 [1] 시점에 만든다.
- ❌ 기존 vendor signature 보유 바이너리(그룹 C, 68개)를 재서명하지 않는다.
- ❌ Real-ESRGAN 저자에게 추가 문의 메일을 보내지 않는다.

---

## 0. 정정 사항 (이전 조사 결론 수정)

이전 조사에서 서명 범위를 **"방안 A(Setup / UPCON.exe / unins000.exe 3개만) 기본값"**
으로 기술했다. **이 결론을 철회한다.**

철회 근거 — Microsoft 공식 개발자 지침이 앱에 포함되어 실행·로드되는 바이너리
**전체**에 대한 서명을 권고하고 있다:

> "To ensure that users have a seamless experience with Smart App Control enabled,
> developers should sign all their application code with a code signing certificate
> from any certificate authority in the Microsoft Trusted Root Program. Developers
> should include **all binaries, such as exe, dll, temp installer files, scripts,
> and uninstallers.**"

> "Because reputation might not be available for newly published binaries and can
> change over time, **ensuring that you correctly sign all your binaries is the best
> way** to make sure users don't encounter issues when using your app."
> — *Test App Signatures with Smart App Control* (Microsoft Learn, 문서 갱신 2025-10-29)

### 현재 확정 상태

> **최종 서명 범위 미확정. Smart App Control 실측 후 결정.**

"3개만 서명"을 기본값으로 삼지 않는다. 동시에 "전부 서명"도 아직 확정하지 않는다.
전부 서명은 FFmpeg Corresponding Source 대응 추적성에 비용을 발생시키므로
(§6 참조), 실측으로 필요 범위를 확인한 뒤 결정한다.

---

## 1. 자체 서명 인증서(self-signed)의 사용 한계

Smart App Control은 **Microsoft Trusted Root Program에 속한 CA가 발급한 인증서만**
신뢰한다:

> "Code can be signed with any certificate, but **Smart App Control only considers
> certificates issued by trusted providers.**"
> — *Sign your app for Smart App Control compliance* (Microsoft Learn, 갱신 2026-02-10)

따라서:

### ❌ self-signed 인증서로 할 수 없는 것

- **Smart App Control 통과 여부 검증** — self-signed는 SAC가 신뢰하지 않으므로,
  self-signed로 서명한 뒤 "통과했다/막혔다"를 관찰해도 **실제 Public Trust 인증서로
  서명했을 때의 결과를 예측하지 못한다.** SAC 적합성 판정 수단으로 사용 금지.
- SmartScreen 평판 검증 — 공식 문서상 self-signed는 미서명과 동일 취급
  ("Self-signed Certificate — Same behavior as no signature").

### ⭕ self-signed 인증서로 할 수 있는 것 (기술 리허설 전용)

- `scripts/sign_binaries.py` 등 서명 스크립트의 동작·인자·오류 처리 검증
- 타임스탬프 처리의 **모의(mock)/제외 경로** 검증
  (self-signed는 공인 TSA가 타임스탬프를 발급하지 않으므로 `/tr` 생략 또는 모의 처리)
- `signtool verify /pa /v` 기반 서명 검증 스크립트의 동작 검증
- PyInstaller 산출물 서명 후 frozen 앱이 정상 동작하는지 (`packaging/selftest.py`)
- Inno Setup `SignTool=` + `SignedUninstaller=yes` 파이프라인 연결 검증

### 추가 제약: RSA 필수, ECC 불가

> "Smart App Control allows applications signed with RSA-based digital certificates
> to run on protected devices. **It does not currently support elliptic-curve
> cryptography (ECC).**"

→ 인증서 선택 시 **RSA 키 인증서**를 발급받아야 한다. ECC 인증서는 SAC 환경에서
무효다. 리허설용 self-signed 인증서도 RSA로 만들어 파이프라인을 일치시킨다.

---

## 2. 현재 개발 PC의 Smart App Control 상태 (실측, 읽기 전용)

| 항목 | 값 |
|---|---|
| OS | Windows 11 Pro 23H2, Build **22631.6199** |
| 설치일 | 2024-11-08 |
| 도메인/Entra 가입 | 없음 (WORKGROUP, AzureAdJoined=NO) |
| `HKLM\SYSTEM\CurrentControlSet\Control\CI\Policy` → `VerifiedAndReputablePolicyState` | **0** |
| `...\Control\CI\Protected` → `VerifiedAndReputablePolicyStateMinValueSeen` | **0** |
| CodeIntegrity Operational 로그 | 존재, 활성, 1,173건 |
| 그중 SAC 이벤트(3076 평가 / 3077 차단) | **0건** |
| `CiTool.exe` | 존재 (`C:\Windows\system32\CiTool.exe`), `-lp` 는 관리자 권한 필요 |

### → **SAC 상태: Off (꺼짐)**

**설정을 변경하지 않았다.** 위 값은 모두 읽기만 했다.
SAC 이벤트가 0건이라는 것은 이 PC에서 SAC가 한 번도 바이너리를 평가한 적이 없다는 뜻이다.

### 이 PC에서 SAC를 켤 수 있는가

공식 개발자 문서:

> "Configuring Smart App Control to **Off** or **On** (enforcement) is a **one-way
> operation.** You can't change modes by using Windows Settings unless the current
> setting is **Evaluation.**"

현재 상태가 Off이고 `MinValueSeen=0`이므로, **Windows 설정 UI로는 Evaluation이나
On으로 되돌릴 수 없다.**

레지스트리 강제 변경 경로는 문서에 있으나 다음을 요구한다:

```
manage-bde -protectors c: -disable -rebootcount 2      # BitLocker 보호기 일시 해제
MpCmdRun.exe -RemoveDefinitions -DynamicSignatures      # Defender 동적 서명 제거
→ 복구 환경(Advanced Startup) 부팅 → 복구 명령 프롬프트
→ reg load / reg add (VerifiedAndReputablePolicyState, MinValueSeen) / reg unload
→ 재부팅
```

Microsoft도 이 방식에 경고를 붙이고 있다:

> "Smart App Control can be manually configured via the Registry **for testing
> purposes only.** Editing Smart App Control settings in this way could compromise
> the protection it provides."

**판정: 이 방식은 개발 PC에 적용하지 않는다.** BitLocker 보호기 해제 + Defender 정의
제거 + 복구 환경 부팅 + SYSTEM 하이브 직접 편집은 "개발 PC 보안 설정을
영구적으로 변경하지 않는다"는 전제에 정면으로 어긋나며, 되돌리려면 같은 과정을 반복해야 한다.

### 참고: 공식 문서 간 불일치 (판정 보류 — 이 PC에서 확인하지 않는다)

Microsoft 지원 FAQ에는 상충하는 기술이 있다:

> "Recent Windows updates allow Smart App Control to be enabled without requiring a
> clean installation." / "you are able to turn on Smart App Control in the Windows
> Security App settings."
> — *Smart App Control Frequently Asked Questions* (support.microsoft.com)

개발자 문서(2025-10-29)는 "Off → 되돌릴 수 없음", 지원 FAQ는 "다시 켤 수 있음"으로
읽힌다. **어느 쪽이 이 PC에 적용되는지는 레지스트리만으로 판정할 수 없다.**

**이 불일치는 해소하지 않고 미해결로 둔다.** 개발 PC의 SAC 상태를 확인하거나 바꾸는
어떤 행위도 하지 않기로 결정했기 때문이다(§0-1 진행 조건 참조). 실제 SAC 동작은
별도 Win11 테스트 환경에서만 확인한다.

---

## 3. Microsoft 공식 SAC 테스트 방법

출처: *Test App Signatures with Smart App Control* (Microsoft Learn, 갱신 2025-10-29)

| 방법 | SAC 요구 상태 | 차단 여부 | 이 PC 적용 가능? |
|---|---|---|---|
| ① Enforcement 모드 직접 테스트 | **On** | 실제 차단 | ❌ Off에서 전환 불가 |
| ② Evaluation + `SmartAppControlAudit.bin` | **Evaluation** | 감사만 | ❌ Evaluation 진입 불가 |
| ③ **`SmartAppControlAuditNoISG.bin`** | **Evaluation 또는 Off** | 감사만 | 🟡 **기술적으로는 가능** |

### ③ NoISG 감사 정책 — 이 STEP의 핵심 후보

> "Use this policy to test your own apps as a developer."
> "This policy checks binaries and scripts against Smart App Control in evaluation
> mode, **without checking the Intelligent Security Graph.** It means that **only apps
> that a trusted certificate properly signs are allowed** without audit events."
> "**You can apply this policy even when you set Smart App Control to Off.**"

적용 절차:

```
1. SAC가 Evaluation 또는 Off 인지 확인
2. mountvol S: /S                                        (관리자)
3. SmartAppControlAuditNoISG.bin 을
   S:\efi\microsoft\boot\cipolicies\active\{5283AC0F-FFF1-49AE-ADA1-8A933130CAD6}.cip
   로 복사                                                (EFI 시스템 파티션 쓰기)
4. citool.exe -r                                          (정책 새로고침)
검증: citool.exe -lp 출력에 VerifiedAndReputableDesktopEvaluationAuditNoISG
정책 파일: https://aka.ms/sacauditpolicies
```

결과 확인:

> "Smart App Control logs evaluation mode events with **event ID 3076**, and
> enforcement mode events with **event ID 3077**."
> 위치: 이벤트 뷰어 > 응용 프로그램 및 서비스 로그 > Microsoft > Windows >
> **CodeIntegrity > Operational**

> ⚠️ "The CodeIntegrity event log **only records information about blocked or audited
> files.** It doesn't log which software installations fail or why... To troubleshoot
> installation failures, you must review the 3076 and 3077 events to identify which
> specific files within the installer were blocked."

### 이 방법의 성격과 한계

- **차단하지 않는다.** 감사 이벤트만 남기므로 앱 동작에 영향이 없다.
- **실제 SAC보다 엄격하다.** ISG(클라우드 평판)를 끄므로 "서명만으로 통과하는가"를
  본다. 즉 **최악 조건 기준선**이며, 여기서 통과하면 실제 SAC에서도 통과한다.
  우리가 알고 싶은 것("어떤 바이너리에 서명이 필요한가")에 정확히 대응한다.
- 정적 목록이 아니라 **실제 로드된 바이너리**를 기록하므로, PE 분석으로는 알 수 없는
  런타임 로드·설치 중 임시 추출 파일까지 잡아낸다. 이것이 정적 분석 대비 고유한 가치다.

### ❌ 이 PC에서 실행하지 않는 이유

③은 SAC가 Off여도 적용 가능하지만, **EFI 시스템 파티션에 부팅 단계 코드 무결성 정책
파일을 기록**하는 작업이다. 감사 전용이고 파일 삭제 + `citool -r`로 되돌릴 수 있으나:

1. 지시된 전제는 "개발 PC 보안 설정을 영구적으로 변경하지 않는다"이다. ESP 부트 정책
   디렉터리 쓰기가 이 경계 안쪽이라고 단정할 수 없다.
2. 부팅 경로에 관여하는 변경이므로 실패 시 영향 범위가 애플리케이션 수준을 넘는다.
3. 이 PC는 UPCON 개발·빌드·배포 검증의 유일한 환경이다. 여기에 리스크를 걸 이유가 없다.

> **결론: 별도 Windows 11 테스트 환경(VM)이 필요하다.**

---

## 4. UPCON 실행 체인과 SAC 평가 대상 (실측 기반)

SAC는 **바이너리를 로드하는 시점마다** 평가한다
("Smart App Control evaluates binaries **as it loads them**").
따라서 "UPCON.exe가 실행된다"가 아니라 "각 단계에서 무엇이 새로 로드되는가"가 기준이다.

### 4-1. 코드 경로 (소스 실측)

| 단계 | 호출 지점 | 생성/로드되는 프로세스·모듈 |
|---|---|---|
| ① 설치 | `UPCON_Setup_0.3.0.exe` | Setup 본체 + **Inno Setup이 `%TEMP%`에 추출하는 임시 바이너리** + `unins000.exe` 생성 |
| ② 앱 시작 | `UPCON.exe` | `UPCON.exe` → `python312.dll`, Qt6 DLL 44개, PSF 서명 확장모듈, **미서명 `_cmsgpack.pyd` / `speedups.pyd`**, **미서명 `libcrypto-3-x64.dll` / `libssl-3-x64.dll`** |
| ③ 영상 probe | `upcon/core/probe.py:99` → `subprocess.run` | **새 프로세스 `ffprobe.exe`** → `avformat/avcodec/avutil/avfilter/swscale/swresample/avdevice` **7개 DLL 전부** |
| ④ 디코딩 | `upcon/core/ffmpeg.py:75` → `Popen` | **새 프로세스 `ffmpeg.exe`** → 동일 7개 DLL |
| ⑤ 인코더 능력 검사 | `upcon/core/ffmpeg.py:94` → `subprocess.run` | `ffmpeg.exe` 추가 실행 (h264_nvenc / h264_amf / h264_qsv 실테스트) |
| ⑥ Real-ESRGAN | `upcon/providers/local_ncnn.py:121, 351` | **새 프로세스 `realesrgan-ncnn-vulkan.exe`** → **`vcomp140.dll`(앱 동봉)** + `vulkan-1.dll`(OS/드라이버) |
| ⑦ 인코딩 | `upcon/core/ffmpeg.py:143` → `Popen` | `ffmpeg.exe` → 7개 DLL (+ HW 인코딩 시 드라이버 DLL) |
| ⑧ 결과 열기 | `main_window.py:374,380` | `explorer.exe` (OS, Microsoft 서명) |
| ⑨ 제거 | `unins000.exe` | **미서명** |

바이너리 탐색 규칙: `upcon/core/binaries.py` — 앱 동봉 `bin/` 우선, 없으면 시스템 PATH.
배포본은 항상 `bin/`을 포함하므로 **동봉 바이너리가 실행된다.**

### 4-2. PE import 실측 — 앱 내부 의존 관계

| 바이너리 | 앱 내부에서 로드하는 모듈 |
|---|---|
| `UPCON.exe` | (정적 import는 전부 OS DLL) — 실행 후 PyInstaller 부트로더가 `_internal`의 모듈을 동적 로드 |
| `ffmpeg.exe` / `ffprobe.exe` | `avcodec-62`, `avdevice-62`, `avfilter-11`, `avformat-62`, `avutil-60`, `swresample-6`, `swscale-9` — **7개 전부** |
| `avcodec-62.dll` | `avutil-60`, `swresample-6` |
| `avformat-62.dll` | `avcodec-62`, `avutil-60` |
| `avfilter-11.dll` | `avcodec-62`, `avformat-62`, `avutil-60`, `swresample-6`, `swscale-9` |
| `avdevice-62.dll` | `avcodec-62`, `avfilter-11`, `avformat-62`, `avutil-60` |
| `swscale-9.dll` / `swresample-6.dll` | `avutil-60` |
| `realesrgan-ncnn-vulkan.exe` | `vcomp140.dll` (Microsoft 서명 — 보존 대상) |
| `libssl-3-x64.dll` | `libcrypto-3-x64.dll` |
| `_cmsgpack.pyd` / `speedups.pyd` | `python312.dll`, `vcruntime140.dll` (모두 서명됨) |

**중요**: `ffmpeg.exe`는 실행 즉시 미서명 FFmpeg DLL **7개를 전부** 로드한다.
즉 ③~⑦ 어느 단계든 발생하면 7개 DLL이 모두 SAC 평가 대상이 된다.
"`ffmpeg.exe`만 서명하고 DLL은 두는" 절충안은 성립하지 않는다.

### 4-3. 런타임 동적 로드 (문자열 실측)

| 바이너리 | 동적 로드 후보 DLL | 제공자 |
|---|---|---|
| `realesrgan-ncnn-vulkan.exe` | `vulkan-1.dll` | OS / GPU 드라이버 |
| `avcodec-62.dll` | `nvcuda.dll`, `nvencodeapi64.dll`, `d3d11.dll`, `mfplat.dll` | NVIDIA 드라이버 / OS |
| `avutil-60.dll` | `amfrt64.dll`, `nvcuda.dll`, `vulkan-1.dll`, `d3d9/11/12.dll`, `dxgi.dll` | AMD·NVIDIA 드라이버 / OS |

→ **GPU/드라이버 DLL은 전부 OS 또는 드라이버 벤더 제공이며 UPCON이 배포하지 않는다.**
각 벤더(NVIDIA/AMD/Microsoft)가 서명하므로 UPCON의 서명 책임 범위 밖이다.

### 4-4. SAC 평가 가능성 정리

| 그룹 | SAC가 평가할 가능성 | 근거 |
|---|---|---|
| Setup / `UPCON.exe` / `unins000.exe` | **높음** — 사용자가 직접 실행 | 실행 파일 로드 시점 평가 |
| Inno Setup `%TEMP%` 추출 임시 파일 | **높음 (미확인)** | Microsoft가 "temp installer files"를 명시적으로 서명 대상에 포함. **실측 필요** |
| FFmpeg 7 DLL + `ffmpeg.exe` + `ffprobe.exe` | **높음** — 자식 프로세스 + 그 DLL | ③~⑦에서 반드시 로드 |
| `realesrgan-ncnn-vulkan.exe` | **높음** — 자식 프로세스 | ⑥에서 반드시 로드 |
| `libcrypto/libssl`, 미서명 `.pyd` 2개 | **중간** — `UPCON.exe` 프로세스 내 DLL 로드 | 클라우드 모드/msgpack 경로 사용 시 |
| Qt/PSF/Microsoft 서명 68개 | 평가되나 **통과** | 신뢰 CA 서명 보유 |
| GPU 드라이버 DLL | 평가되나 **통과** | 벤더 서명 |

---

## 5. 서명 범위 3그룹 분류

> **현 시점에서 B 그룹을 실제로 재서명하지 않는다** (인증서 없음, 이 STEP 범위 밖).

### A. 반드시 서명해야 할 UPCON 자체 바이너리 — 3개 (+α)

| 파일 | 크기 | 현재 |
|---|---:|---|
| `UPCON.exe` | 6,141,334 | NotSigned |
| `UPCON_Setup_<ver>.exe` | 104,179,763 | NotSigned |
| `unins000.exe` (설치 시 생성) | 4,460,894 | NotSigned |
| + Inno Setup `%TEMP%` 추출 임시 파일 | ? | **실측 필요** |

`unins000.exe`는 Inno Setup 컴파일 시점에만 서명 가능
(`SignTool=` + `SignedUninstaller=yes`).

### B. SAC 호환성을 위해 서명이 필요할 가능성이 있는 미서명 third-party 바이너리 — 12개

| 파일 | 크기 | 출처 |
|---|---:|---|
| `_internal\bin\avcodec-62.dll` | 118,089,216 | FFmpeg n8.1.2 GPL shared (BtbN) |
| `_internal\bin\avfilter-11.dll` | 36,607,488 | 〃 |
| `_internal\bin\avformat-62.dll` | 22,869,504 | 〃 |
| `_internal\bin\swscale-9.dll` | 12,823,040 | 〃 |
| `_internal\bin\avdevice-62.dll` | 4,048,384 | 〃 |
| `_internal\bin\avutil-60.dll` | 3,011,584 | 〃 |
| `_internal\bin\swresample-6.dll` | 733,696 | 〃 |
| `_internal\bin\ffmpeg.exe` | 540,672 | 〃 |
| `_internal\bin\ffprobe.exe` | 226,816 | 〃 |
| `_internal\bin\realesrgan-ncnn-vulkan.exe` | 6,161,408 | Real-ESRGAN-ncnn-vulkan |
| `_internal\libcrypto-3-x64.dll` | 5,801,116 | OpenSSL (Python 휠 동봉) |
| `_internal\libssl-3-x64.dll` | 1,026,716 | 〃 |
| `_internal\msgpack\_cmsgpack.cp312-win_amd64.pyd` | 126,464 | PyPI 휠 |
| `_internal\websockets\speedups.cp312-win_amd64.pyd` | 11,264 | 〃 |

(A + B 미서명 합계 = 15개 / 218,218,702 bytes)

**B에 대한 판단 보류 사유**: 서명하면 배포 바이너리의 SHA-256이 upstream 원본과
달라져 FFmpeg Corresponding Source 대응 추적성에 단서가 붙는다(§6). 반면 서명하지
않으면 SAC 환경에서 ③~⑦ 단계가 실패할 수 있다. **실측으로 결정한다.**

### C. 기존 vendor signature를 그대로 보존해야 하는 바이너리 — 68개

| 서명자 | 개수 |
|---|---:|
| `CN=The Qt Company Oy` | 44 |
| `CN=Python Software Foundation` | 21 |
| `Microsoft Windows Software Compatibility Publisher` (VCRUNTIME140 계열) | 2 |
| `CN=Microsoft Corporation` (`vcomp140.dll`) | 1 |

**절대 재서명하지 않는다.** 재서명은 원 서명을 대체하여 검증 가능한 출처 정보를 파괴한다.

> "**Do not modify signed files** — Avoid modifying files after signing as doing so
> can break the signature" — *SmartScreen reputation for Windows app developers*

→ 서명 스크립트는 **"이미 Valid 서명이 있으면 건너뛴다"를 하드 가드로** 구현하고,
서명 전후 68개 파일의 서명자·해시 동일성을 회귀 테스트로 검증한다.

---

## 6. FFmpeg Corresponding Source 추적성 설계 (B 그룹 서명 시)

**현재 FFmpeg 바이너리는 수정하지 않는다.** 아래는 향후 B를 서명하기로 결정할 경우에
대비한 설계 기록이다.

### 문제

`scripts/fetch_binaries.py`는 FFmpeg zip을 SHA-256
`b5959e30cfe756a10d40ed0323e3d16e02be11379a46320bbe635f0e7787f109` 로 고정 검증한다.
Authenticode 서명은 PE에 인증서 블록을 덧붙이므로 **배포 바이너리의 해시가 upstream
원본과 달라진다.** 그러면 "이 Corresponding Source로 이 바이너리가 나온다"는 대응
관계를 원본 해시만으로는 증명할 수 없다.

### 설계: 이중 해시 기록

배포 구성요소마다 **두 해시를 모두 기록**한다.

```
component          : ffmpeg.exe
upstream source    : BtbN/FFmpeg-Builds autobuild-2026-09-15-13-18
                     ffmpeg-n8.1.2-53-g1005b294ff-win64-gpl-shared-8.1.zip
upstream sha256    : <zip 내 원본 파일의 SHA-256>          # 서명 전
distributed        : dist/UPCON/_internal/bin/ffmpeg.exe
distributed sha256 : <UPCON 서명 후 SHA-256>                # 서명 후
modification       : Authenticode signature appended only.
                     No change to code, data, or resources.
verify             : signtool verify /pa /v <file>
                     서명 블록 제거 시 upstream sha256 과 일치해야 한다.
```

반영 대상 (**이번 STEP에서는 변경하지 않음**):

| 대상 | 추가 내용 |
|---|---|
| `scripts/build_ffmpeg_source_package.py` 의 `BUILD-INFO` / `SOURCE-MANIFEST` | 구성요소별 upstream/distributed 이중 해시 열 |
| `docs/THIRD_PARTY_NOTICES.md` | "배포 바이너리는 upstream 바이너리에 Authenticode 서명만 부가한 것" 명시 |
| `scripts/fetch_binaries.py` | upstream 해시 검증은 **서명 전 단계에서** 수행 (현행 유지) |
| 서명 스크립트 | 서명 전 해시를 기록 후 서명, 두 해시를 매니페스트에 출력 |

이 방식으로 **"서명 = 부가 정보이며 코드는 upstream과 동일"** 을 기계적으로 검증
가능하게 유지한다. 다만 이는 추적성 설계이며 GPL 준수에 대한 법률 판단이 아니다.

---

## 7. SAC 실측 테스트 계획 (별도 Win11 환경 필요)

### 7-1. 환경 요구사항

| 항목 | 요구 |
|---|---|
| OS | Windows 11 22H2 이상 (SAC 지원 빌드) |
| 형태 | **폐기 가능한 VM** (스냅샷/체크포인트 필수) |
| 제외 조건 | 도메인/Entra 미가입, 개발자 모드 꺼짐, S 모드 아님, 선택적 진단 데이터 켜짐 |
| 디스크 | VM 30~40GB. **현 개발 PC C: 여유 44.2GB — 빠듯함. 별도 디스크 권장** |
| 하이퍼바이저 | 현 PC `HypervisorPresent=False` (Hyper-V 미실행). 활성화 시 재부팅 필요 |
| GPU | ⚠️ VM에서는 Vulkan/NVENC 미동작 가능 → **⑥⑦ 단계 GPU 경로는 별도 물리 PC 필요** |

> Windows Sandbox는 **부적합**하다. NoISG 정책 적용에 EFI 파티션 쓰기 + 재부팅이
> 필요한데 Sandbox는 재부팅을 지원하지 않고 종료 시 상태가 사라진다.

### 7-2. 테스트 대상 (최소)

| # | 대상 | 확인할 것 |
|---|---|---|
| 1 | `UPCON_Setup_0.3.0.exe` | 설치 자체 + **`%TEMP%` 추출 임시 바이너리** 감사 이벤트 |
| 2 | `UPCON.exe` | 앱 시작. `libcrypto`/`libssl`/`.pyd` 로드 여부 |
| 3 | `ffprobe.exe` + FFmpeg DLL 7개 | 영상 probe 단계 |
| 4 | `ffmpeg.exe` + FFmpeg DLL 7개 | 디코딩·인코더 능력 검사·인코딩 단계 |
| 5 | `realesrgan-ncnn-vulkan.exe` | 업스케일 단계 (GPU 필요) |
| 6 | `libcrypto-3-x64.dll` / `libssl-3-x64.dll` | 클라우드 모드 진입 시 |
| 7 | 미서명 `.pyd` 2개 | msgpack/websockets 경로 |
| 8 | `unins000.exe` | 제거 단계 |
| 9 | Qt/PSF/MS 서명 68개 | **감사 이벤트가 발생하지 않아야 정상** (대조군) |

### 7-3. 절차

```
Phase 0  VM 준비 → 체크포인트 "clean"
Phase 1  현행 미서명 Portable/Installer 반입 (서명 없음)
Phase 2  SmartAppControlAuditNoISG.bin 적용
           mountvol S: /S
           copy → S:\efi\microsoft\boot\cipolicies\active\{5283AC0F-...}.cip
           citool.exe -r
         검증: citool.exe -lp → VerifiedAndReputableDesktopEvaluationAuditNoISG
Phase 3  CodeIntegrity Operational 로그 기준시각 기록 후 전 코드 경로 실행
           설치 → 앱 시작 → probe → 업스케일 1건 → 결과 저장 → 제거
Phase 4  Event ID 3076 전수 수집 → 파일 경로별 집계
Phase 5  A/B 그룹 확정. 대조군(68개)에서 이벤트가 나오면 분석 중단하고 원인 규명
Phase 6  체크포인트 "clean" 롤백
```

⚠️ **테스트 실행 규칙** (기존 사고 재발 방지):
- 설치/제거는 **PowerShell 또는 subprocess로만** 실행. **Git Bash로 `/VERYSILENT` 등
  Windows 스위치 전달 금지** (경로 변환으로 대화형 제거가 실행된 사고 있음)
- VM 내에서만 수행. 개발 PC의 `%LOCALAPPDATA%\UPCON` 및 실제 fal 자격 증명에 접근 금지
- 전체 화면 캡처 금지. 필요 시 해당 window/widget만 캡처

### 7-4. 판정 기준

| 관측 | 결론 |
|---|---|
| A 그룹에서만 3076 발생 | A만 서명해도 충분 |
| A + FFmpeg/ncnn 에서 3076 발생 | **B 그룹 서명 필요** → §6 이중 해시 설계 적용 |
| Setup의 `%TEMP%` 임시 파일에서 3076 발생 | Inno Setup 임시 바이너리 서명 방법 추가 조사 필요 |
| 68개 대조군에서 3076 발생 | 예상 밖 — 정책 적용 오류 가능성. 원인 규명 후 재시도 |

### 7-5. 선행 조건

**이 테스트는 인증서 없이 수행 가능하다.** 미서명 현행 빌드를 그대로 넣고
"무엇이 감사에 걸리는가"만 본다. 따라서 **인증서 구매 결정 이전에 수행해야 하며,
그 결과가 인증서 서명 횟수 요구량(A=3~4개 vs A+B=17~18개)에 직접 영향을 준다.**

---

## 출처 (확인일 2026-09-16)

- [Test App Signatures with Smart App Control](https://learn.microsoft.com/en-us/windows/apps/develop/smart-app-control/test-your-app-with-smart-app-control) — 문서 갱신 2025-10-29
- [Sign your app for Smart App Control compliance](https://learn.microsoft.com/en-us/windows/apps/develop/smart-app-control/code-signing-for-smart-app-control) — 갱신 2026-02-10
- [Smart App Control overview](https://learn.microsoft.com/en-us/windows/apps/develop/smart-app-control/overview)
- [Smart App Control Frequently Asked Questions](https://support.microsoft.com/en-us/windows/security/threat-malware-protection/smart-app-control-frequently-asked-questions)
- [SmartScreen reputation for Windows app developers](https://learn.microsoft.com/en-us/windows/apps/package-and-deploy/smartscreen-reputation) — 갱신 2026-08-17
- [SAC audit policies (aka.ms/sacauditpolicies)](https://aka.ms/sacauditpolicies)
