!include "MUI2.nsh"
Name "사내 발주 관리"
OutFile "SaenaeOrderManager_Setup.exe"
InstallDir "$PROGRAMFILES64\SaenaeOrderManager"
RequestExecutionLevel admin
!define MUI_ABORTWARNING
!insertmacro MUI_PAGE_DIRECTORY
!insertmacro MUI_PAGE_INSTFILES
!insertmacro MUI_UNPAGE_CONFIRM
!insertmacro MUI_UNPAGE_INSTFILES
!insertmacro MUI_LANGUAGE "Korean"

Section "Install"
  SetOutPath "$INSTDIR"
  File /r "dist\SaenaeOrderManager\*.*"
  CreateDirectory "$SMPROGRAMS\사내 발주 관리"
  CreateShortcut "$SMPROGRAMS\사내 발주 관리\사내 발주 관리.lnk" "$INSTDIR\SaenaeOrderManager.exe"
  CreateShortcut "$DESKTOP\사내 발주 관리.lnk" "$INSTDIR\SaenaeOrderManager.exe"
  WriteUninstaller "$INSTDIR\Uninstall.exe"
  ; 기본 자동 실행은 앱 내부 설정에서 사용자가 선택하도록 둠
SectionEnd

Section "Uninstall"
  Delete "$DESKTOP\사내 발주 관리.lnk"
  Delete "$SMPROGRAMS\사내 발주 관리\사내 발주 관리.lnk"
  RMDir "$SMPROGRAMS\사내 발주 관리"
  Delete "$INSTDIR\Uninstall.exe"
  RMDir /r "$INSTDIR"
SectionEnd
