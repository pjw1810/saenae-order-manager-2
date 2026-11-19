발주 관리 - GitHub 자동 빌드 프로젝트

이 저장소는 GitHub Actions가 Windows 클라우드 러너에서 EXE와 Setup.exe를 빌드하도록 구성되어 있습니다.
빌드용 PC에도 Python을 설치할 필요가 없습니다. (GitHub Actions가 빌드 중 Python을 임시 설치합니다.)
설치 후 프로그램을 사용하는 사내 PC에는 Python이 필요하지 않습니다.

[GitHub에 올리는 방법]
1. GitHub에 로그인합니다.
2. 우측 상단 + -> New repository를 선택합니다.
3. 저장소 이름 예: saenae-order-manager
4. Private를 선택하고 Create repository를 누릅니다.
5. 이 ZIP의 압축을 풀고 모든 파일과 .github 폴더를 저장소에 업로드합니다.
   중요: .github/workflows/windows-build.yml 경로가 정확해야 합니다.
6. 저장소의 Actions 탭을 엽니다.
7. "Build Windows installer" 워크플로를 선택합니다.
8. Run workflow -> Run workflow를 누릅니다.
9. 실행이 완료되면 해당 실행 기록의 Artifacts에서
   SaenaeOrderManager-Windows-Installer를 다운로드합니다.
10. 압축을 풀면 Setup.exe가 있습니다.

[버전 태그로 Release 만들기]
저장소의 Releases -> Draft a new release에서 태그 v1.0.0을 만들고 Publish release를 누르면
워크플로가 실행되어 Setup.exe를 Release 자산으로 게시합니다.
(또는 저장소에서 v1.0.0 태그를 push해도 됩니다.)

[프로그램 설정]
- 메인 PC: 프로그램 상단에서 '이 PC를 메인 서버로 사용' 체크 후 설정 저장
- 메인 PC의 사내 IPv4 주소 확인 (Windows 명령 프롬프트에서 ipconfig)
- Windows 방화벽에서 사내 Private 네트워크에 한해 TCP 8765 인바운드 허용
- 다른 PC: 서버 주소에 메인 PC IPv4 입력 후 설정 저장
- 모든 PC에서 'Windows 로그인 시 자동 실행' 체크 후 설정 저장 가능

[중요한 운영 주의]
- 발주 데이터는 GitHub가 아니라 메인 PC의 로컬 SQLite DB에 저장됩니다.
- 메인 PC는 업무 중 켜져 있어야 하며 절전/최대절전 방지를 권장합니다.
- DB 파일: %LOCALAPPDATA%\\SaenaeOrderManager\\orders.sqlite3
- 서버 시작 시 자동 백업, 매일 오후 6시 자동 백업, 프로그램 종료 전 백업을 수행합니다.
- 수동 백업은 프로그램의 [백업하기] 버튼으로 실행할 수 있습니다.
- 백업 위치: %LOCALAPPDATA%\SaenaeOrderManager\backups\ (최근 30일 백업 보관)
- 이 초기 버전은 사내 신뢰 LAN 전용입니다. 공유기 포트 포워딩이나 인터넷 공개를 하지 마세요.
- 로그인/사용자별 권한, 암호화 통신, 감사 로그, 서버 자동 백업은 아직 포함되지 않았습니다.
- 모든 PC에서 수정할 수 있도록 설정되어 있으며 동시 수정 시 마지막 저장이 반영됩니다.
- 메인 서버 PC에서는 [이 PC를 메인 서버로 사용]과 [Windows 로그인 시 자동 실행]을 모두 체크하고 [설정 저장]을 누르세요.
- 자동 실행은 Windows 사용자 로그인 후 프로그램을 실행합니다. 따라서 메인 PC는 업무 중 로그인 상태를 유지해야 합니다.
- 매일 오후 6시 자동 백업이 실행되며, 프로그램을 정상 종료할 때도 백업합니다.

[보안]
저장소는 Private로 만드는 것을 권장합니다. 단, 설치 파일을 받을 사람은 GitHub 저장소 권한이 필요할 수 있습니다.
사내 사용자에게 편하게 배포하려면 GitHub Release 파일을 별도 사내 공유 폴더로 전달하는 방법도 있습니다.

[재설치 없는 자동 업데이트]
- 설치 후 프로그램이 GitHub의 최신 Release를 확인하고 새 버전이 있으면 알림을 표시합니다.
- 상단 [업데이트 확인] 버튼으로 수동 확인도 가능합니다.
- 업데이트 자산: OrderManager_Update.zip, OrderManager_Update.zip.sha256
- ZIP 다운로드 후 SHA-256 검증을 통과해야 Updater.exe가 실행됩니다.
- Updater는 실행 중인 프로그램이 종료된 뒤 설치 폴더를 백업하고 파일을 교체한 후 프로그램을 다시 실행합니다.
- 교체 실패 시 설치 폴더를 백업본으로 복구합니다. 정상 업데이트 후에도 .OrderManager_rollback_* 폴더를 남겨 수동 복구할 수 있습니다.
- orders.sqlite3, config.json, backups는 %LOCALAPPDATA%\\SaenaeOrderManager에 있으며 업데이트 패키지 대상이 아닙니다.
- update_config.json도 같은 사용자 데이터 폴더에 최초 설치 시에만 생성되며, 재설치/업데이트 시 기존 설정을 덮어쓰지 않습니다.

[GitHub 저장소 설정]
1. update_config.json의 github_repo를 실제 OWNER/REPOSITORY로 변경합니다.
2. 최초 설치 전에 설정을 바꾸거나, 설치 후 %LOCALAPPDATA%\\SaenaeOrderManager\\update_config.json을 수정합니다.
3. 소스 저장소에서 v1.0.1처럼 v로 시작하는 태그를 push하면 Actions가 빌드 후 Release에 설치 파일과 업데이트 패키지를 게시합니다.
4. 메인 서버 PC를 먼저 업데이트하고 정상 연결을 확인한 뒤 직원 PC를 업데이트하세요.
5. GitHub Release 자동 다운로드는 기본적으로 공개 Release를 전제로 합니다. 비공개 저장소 Release를 사용하려면 별도 인증 배포 구성이 필요합니다.

[업데이트 주의]
- 업데이트 중에는 해당 PC의 Order Manager가 종료됩니다. 메인 서버 업데이트 중 직원 PC의 발주 작업은 잠시 중단됩니다.
- 업데이트는 프로그램 설치 폴더만 교체하며, 사용자 데이터 폴더를 삭제하지 않습니다.
- Windows UAC 권한 승인이 필요할 수 있습니다.
- 이 프로젝트는 소스/정적 검사만 수행했으며 실제 Windows 설치, UAC, LAN 동시접속 및 다중 PC 업데이트는 별도 현장 검증이 필요합니다.
