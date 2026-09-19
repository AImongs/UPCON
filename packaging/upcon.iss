; UPCON Windows Installer (Inno Setup 6) - 개발/테스트 검증용
;
; 빌드: .\.venv\Scripts\python packaging\build_installer.py
;   (버전을 upcon/version.py 에서 읽어 /DMyAppVersion 으로 넘긴다. ISCC 를 직접 실행하면
;    버전이 0.0.0-dev 로 표시되어 잘못된 빌드임이 바로 드러난다.)
;
; 설치 대상은 검증된 Portable 빌드(dist\UPCON)를 '그대로' 넣는다. 내부 구조를 재조립하지 않는다.
; 사용자 데이터(config.json, queue.json, logs, temp, 결과 영상)는 설치 폴더가 아니라
; %LOCALAPPDATA%\UPCON 에 저장되고, fal.ai API Key 는 Windows 자격 증명 관리자에 남는다.

#define MyAppName "UPCON"
#define MyAppExeName "UPCON.exe"
#define MyAppPublisher "UPCON"
#define MyAppDescription "AI Video Upscaler"

#ifndef MyAppCopyright
  #define MyAppCopyright "UPCON"
#endif

; 위저드 이미지 (scripts/make_installer_images.py 가 upcon.png 에서 생성).
; 파일이 없으면 Inno 기본 이미지로 빌드된다.
#define WizLarge "installer\wizard-large-164x314.bmp"
#define HaveWizardImages FileExists(AddBackslash(SourcePath) + WizLarge)

#ifndef MyAppVersion
  #define MyAppVersion "0.0.0-dev"
#endif

#define SrcDir "..\dist\UPCON"
#define IconRel "..\upcon\resources\upcon.ico"
#define HaveIcon FileExists(AddBackslash(SourcePath) + IconRel)

; HOTFIX-2 진단용 빌드 전용. /DDIAGBUILD 를 ISCC 에 넘길 때만 켜진다 — 기본(정식) 빌드는
; 이 심볼이 정의되지 않으므로 아래 #ifdef DIAGBUILD 블록이 전부 그대로 스킵되고, 정식
; 배포본은 이번 변경 이전과 완전히 같은 결과물을 만든다(파일명·[Files]·[Icons] 전부 무변화).
#define DiagBat "diag\UPCON_Software_Encoder_Test.bat"
#define DiagNcnnBat "diag\UPCON_NCNN_DIAG.bat"

[Setup]
; AppId 는 절대 바꾸지 않는다. 이 값이 같아야 향후 버전이 기존 설치 위에 업그레이드된다.
AppId={{B04C51EA-BF7B-4CEC-9D05-F18BD9D99E54}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppVerName={#MyAppName} {#MyAppVersion}
; 예전엔 여기 버전이 하드코딩돼 있어 /DMyAppVersion 을 바꿔도 EXE 파일 속성(자세히 탭)의
; 파일 버전만 안 바뀌는 불일치가 있었다 — {#MyAppVersion} 으로 단일 소스에 맞춘다.
VersionInfoVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
UninstallDisplayName={#MyAppName} {#MyAppVersion}
AppCopyright={#MyAppCopyright}
VersionInfoProductName={#MyAppName}
VersionInfoProductTextVersion={#MyAppVersion}
VersionInfoDescription={#MyAppName} Setup
VersionInfoCompany={#MyAppPublisher}
VersionInfoCopyright={#MyAppCopyright}

; --- 권한 정책 ---
; per-user 설치. 관리자 권한(UAC)을 요구하지 않는다.
; {autopf} 는 PrivilegesRequired=lowest 에서 %LOCALAPPDATA%\Programs 로 해석된다.
PrivilegesRequired=lowest
DefaultDirName={autopf}\{#MyAppName}
DefaultGroupName={#MyAppName}
DisableProgramGroupPage=yes
AllowNoIcons=yes

; --- 출력 ---
OutputDir=..\dist-installer
#ifdef DIAGBUILD
OutputBaseFilename=UPCON_Setup_{#MyAppVersion}_DIAG
#else
OutputBaseFilename=UPCON_Setup_{#MyAppVersion}
#endif
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
; 브랜드가 보이도록 환영 화면을 켠다 (modern 스타일 기본값은 숨김).
DisableWelcomePage=no
DisableReadyPage=no
ShowLanguageDialog=auto

#if HaveWizardImages
WizardImageFile=installer\wizard-large-164x314.bmp,installer\wizard-large-192x386.bmp,installer\wizard-large-246x471.bmp,installer\wizard-large-328x628.bmp
WizardSmallImageFile=installer\wizard-small-55x55.bmp,installer\wizard-small-64x68.bmp,installer\wizard-small-83x80.bmp,installer\wizard-small-110x110.bmp
#endif
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible

; 설치/제거 중 UPCON 이 실행 중이면 재시작 관리자로 감지해 닫도록 안내한다.
CloseApplications=yes
RestartApplications=no

#if HaveIcon
SetupIconFile={#IconRel}
UninstallDisplayIcon={app}\{#MyAppExeName}
#else
; upcon.ico 가 아직 없다 -> Inno 기본 아이콘으로 빌드된다.
UninstallDisplayIcon={app}\{#MyAppExeName}
#endif

[Languages]
Name: "korean"; MessagesFile: "compiler:Languages\Korean.isl"
Name: "english"; MessagesFile: "compiler:Default.isl"

[Messages]
korean.WelcomeLabel1=%n%nUPCON 설치
korean.WelcomeLabel2=AI 영상의 해상도를 쉽고 빠르게 향상시키는 UPCON 설치 프로그램입니다.
korean.FinishedHeadingLabel=%n%nUPCON 설치 완료
english.WelcomeLabel1=%n%nInstall UPCON
english.WelcomeLabel2=Setup will install UPCON, a tool that upscales AI video to a higher resolution.
english.FinishedHeadingLabel=%n%nUPCON installation complete

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"; Flags: unchecked

[Files]
; 검증된 Portable 빌드를 통째로 설치 (UPCON.exe + _internal\ = bin, models, docs, 라이선스)
Source: "{#SrcDir}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs
#ifdef DIAGBUILD
; HOTFIX-2 진단 전용: libx264 강제 launcher + ncnn/Vulkan 독립 진단 도구
; (일반 배포본에는 포함되지 않는다)
Source: "{#DiagBat}"; DestDir: "{app}"; Flags: ignoreversion
Source: "{#DiagNcnnBat}"; DestDir: "{app}"; Flags: ignoreversion
#endif

[Icons]
Name: "{group}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; Comment: "{#MyAppDescription}"
#ifdef DIAGBUILD
Name: "{group}\{#MyAppName} - 소프트웨어 인코더 테스트"; Filename: "{app}\UPCON_Software_Encoder_Test.bat"; Comment: "libx264 강제 진단 테스트 (NVENC 미사용, 이번 실행에만 적용)"
Name: "{group}\{#MyAppName} - ncnn Vulkan 진단"; Filename: "{app}\UPCON_NCNN_DIAG.bat"; Comment: "Real-ESRGAN ncnn/Vulkan 단계를 UPCON 파이프라인과 분리해서 직접 테스트"
#endif
Name: "{group}\{cm:UninstallProgram,{#MyAppName}}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; Tasks: desktopicon

[Run]
; skipifsilent 는 Setup 이 스스로 (very) silent 라고 인식할 때만 이 항목을 건너뛴다 (공식 동작,
; ISetup.chm topic_runsection 확인됨). Check: 는 같은 정책을 [Code] 의 WizardSilent() 로 독립적으로
; 한 번 더 강제하는 2중 안전장치다 — skipifsilent 플래그가 실수로 지워지는 미래의 편집 실수를 막는다.
; (두 메커니즘 모두 Setup 이 실제로 수신한 명령줄을 근거로 판단하므로, 명령줄 자체가 셸에 의해
; 훼손되는 경우까지는 막지 못한다 — 그건 호출 쪽 책임이다. STEP 8-1/9-2 참고.)
Filename: "{app}\{#MyAppExeName}"; Description: "{cm:LaunchProgram,{#MyAppName}}"; Flags: nowait postinstall skipifsilent; Check: ShouldLaunchAfterInstall

[Code]
// silent/very silent 설치에서는 설치 완료 후 UPCON 을 자동 실행하지 않는다.
// WizardSilent 는 Inno Setup 공식 Pascal Scripting 함수(Prototype: function WizardSilent: Boolean).
function ShouldLaunchAfterInstall(): Boolean;
begin
  Result := not WizardSilent();
end;

// 제거 시 사용자 데이터를 조용히 지우지 않는다. 기본값은 '보존'이며, 명시적으로 동의할 때만 삭제한다.
procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
var
  DataDir: String;
  ResultCode: Integer;
begin
  if CurUninstallStep = usPostUninstall then
  begin
    DataDir := ExpandConstant('{localappdata}\UPCON');
    if DirExists(DataDir) then
    begin
      // 반드시 SuppressibleMsgBox 를 쓴다. 평범한 MsgBox 는 /SUPPRESSMSGBOXES 를 무시하고
      // 무인 제거 시 IDYES 를 돌려주어 사용자 데이터와 API Key 를 말없이 지워버린다.
      // 마지막 인자(IDNO)가 메시지 박스가 억제될 때의 반환값 = '보존'.
      if SuppressibleMsgBox('UPCON 사용자 데이터도 함께 삭제할까요?' + #13#10#13#10 +
                '· 설정(config.json), 대기열(queue.json), 로그' + #13#10 +
                '· Windows 자격 증명 관리자에 저장된 fal.ai API Key' + #13#10#13#10 +
                '업스케일 결과 영상은 원본 옆에 저장되므로 삭제되지 않습니다.' + #13#10 +
                '[아니요]를 선택하면 데이터가 그대로 남아 다시 설치할 때 이어서 쓸 수 있습니다.',
                mbConfirmation, MB_YESNO or MB_DEFBUTTON2, IDNO) = IDYES then
      begin
        DelTree(DataDir, True, True, True);
        // keyring(WinVaultKeyring) 은 Generic 자격 증명 'UPCON' 에 저장한다.
        Exec(ExpandConstant('{cmd}'), '/c cmdkey /delete:UPCON', '', SW_HIDE, ewWaitUntilTerminated, ResultCode);
      end;
    end;
  end;
end;
