; EngineeringOS installer (ALPHA)
;
; Per-user install: no administrator rights, no machine-wide state.
;   program files : $INSTDIR (payload, removed after the install completes)
;   user data     : %USERPROFILE%\.eos  (versions, evidence, baselines, config)
;   launcher      : %USERPROFILE%\.eos\bin\eos.exe
;
; The payload is handed to `eos install`, which is the only code that decides
; what a version directory contains. The installer itself never copies a
; version into place, so the installer and the CLI can never disagree about
; what an installation is.
;
; Two NSIS details that are load-bearing here:
;   * `ExecWait <cmd> $0` - the exit code is taken from the $0 parameter.
;     `Pop $0` does not work in this NSIS build, and an empty $0 compared
;     against 0 reads as "failed", which aborted the install after it had
;     already succeeded.
;   * A silent install ends with an explicit `Quit`: NSIS's normal teardown
;     keeps a message loop alive, and in a session without an interactive
;     desktop the installer never returns.
;
!include "MUI2.nsh"
!include "FileFunc.nsh"

!ifndef VERSION
  !define VERSION "0.2.6-alpha"
!endif
!ifndef PRODUCTVERSION
  !define PRODUCTVERSION "0.2.6.0"
!endif
!ifndef STAGE
  !define STAGE "..\installer\stage"
!endif

Unicode true
RequestExecutionLevel user
SetCompressor /SOLID lzma
SetCompressorDictSize 64

Name "EngineeringOS ${VERSION}"
OutFile "..\installer\out\EngineeringOS-Setup-${VERSION}.exe"
InstallDir "$LOCALAPPDATA\Programs\EngineeringOS"
InstallDirRegKey HKCU "Software\EngineeringOS" "InstallDir"
BrandingText "EngineeringOS ${VERSION} - ALPHA"

VIProductVersion "${PRODUCTVERSION}"
VIAddVersionKey "ProductName" "EngineeringOS"
VIAddVersionKey "FileDescription" "EngineeringOS installer"
VIAddVersionKey "FileVersion" "${PRODUCTVERSION}"
VIAddVersionKey "ProductVersion" "${VERSION}"
VIAddVersionKey "LegalCopyright" "Apache-2.0; ALPHA unsigned build"

!define MUI_ABORTWARNING
!define MUI_ICON "eos.ico"
!define MUI_UNICON "eos.ico"

!insertmacro MUI_PAGE_DIRECTORY
!insertmacro MUI_PAGE_INSTFILES
!define MUI_FINISHPAGE_RUN "$INSTDIR\EngineeringOS.cmd"
!define MUI_FINISHPAGE_RUN_TEXT "Run eos doctor"
!insertmacro MUI_PAGE_FINISH

!insertmacro MUI_UNPAGE_CONFIRM
!insertmacro MUI_UNPAGE_INSTFILES
!insertmacro MUI_UNPAGE_FINISH

!insertmacro MUI_LANGUAGE "English"

Section "EngineeringOS" SecMain
  SectionIn RO
  SetOutPath "$INSTDIR\payload"
  File /r "${STAGE}\*"
  SetOutPath "$INSTDIR"

  ; The product installs itself: same code path as `eos install`, so the
  ; installer cannot produce a layout the CLI would not recognise.
  ; The command runs from a temporary copy so that no file in $INSTDIR is
  ; locked by a running process while the payload is removed afterwards.
  DetailPrint "Installing EngineeringOS ${VERSION}..."
  CopyFiles "$INSTDIR\payload\eos.exe" "$TEMP\eos-setup.exe"
  ; No shell and no redirection: NSIS has no output capture, so the CLI writes
  ; its own log to %USERPROFILE%\.eos\state\install-<version>.log. That file is
  ; the evidence, on the machine, after the installer has exited.
  ; --force: an installer must be able to replace the version it is installing.
  ; Without it a machine that already has the same version refuses the install
  ; and the user is left with a half-finished setup.
  ExecWait '"$TEMP\eos-setup.exe" install --source "$INSTDIR\payload" --version ${VERSION} --add-to-path --force' $0
  Delete "$TEMP\eos-setup.exe"
  ${If} $0 != 0
    ; A message box in a silent install blocks forever: there is nobody to
    ; click it. The install log is the evidence instead.
    IfSilent install_failed_silently
      MessageBox MB_ICONSTOP "EOS install failed (exit $0). Nothing was activated.\n\nSee $PROFILE\.eos\state\install-${VERSION}.log for the reason."
    install_failed_silently:
    Abort
  ${EndIf}

  ; The payload has served its purpose; the installed copy lives in the user
  ; data directory. Keeping a second 140 MB copy would be misleading.
  RMDir /r /REBOOTOK "$INSTDIR\payload"

  WriteRegStr HKCU "Software\EngineeringOS" "InstallDir" "$INSTDIR"
  WriteRegStr HKCU "Software\EngineeringOS" "Version" "${VERSION}"

  CreateDirectory "$SMPROGRAMS\EngineeringOS"
  CreateShortCut "$SMPROGRAMS\EngineeringOS\EngineeringOS.lnk" "$INSTDIR\EngineeringOS.cmd"
  CreateShortCut "$SMPROGRAMS\EngineeringOS\Uninstall.lnk" "$INSTDIR\uninstall.exe"
  CreateShortCut "$DESKTOP\EngineeringOS.lnk" "$INSTDIR\EngineeringOS.cmd"

  WriteUninstaller "$INSTDIR\uninstall.exe"
  File "eos.cmd"
  Rename "$INSTDIR\eos.cmd" "$INSTDIR\EngineeringOS.cmd"
  ClearErrors

  ; Interactive installs continue to the finish page. A silent install has no
  ; page to show, and the normal teardown never returns in a session without
  ; an interactive desktop, so it ends here.
  IfSilent install_silent_done
    Goto install_finished
  install_silent_done:
    Call finish_install
  install_finished:
SectionEnd

Function finish_install
  ; NSIS ends a silent install with status 2 ("error in script") unless the
  ; error level is set explicitly. Reaching this point means the install
  ; command reported success, so the honest code is 0.
  SetErrorLevel 0
  Quit
FunctionEnd

Section "Uninstall"
  ; Remove the installed version and the PATH entry, but keep the evidence and
  ; baselines: a machine's audit trail is not the installer's to delete.
  IfFileExists "$PROFILE\.eos\bin\eos.exe" 0 no_eos
    ExecWait '"$PROFILE\.eos\bin\eos.exe" uninstall' $0
    IfSilent uninstall_no_message
      ${If} $0 != 0
        MessageBox MB_ICONSTOP "EOS uninstall reported an error (exit $0). Program files were still removed."
      ${EndIf}
    uninstall_no_message:
  no_eos:
  ; The CLI uninstall cannot delete the launcher it is running from; this
  ; process can, because it is not that executable.
  Delete "$PROFILE\.eos\bin\eos.exe"
  Delete "$PROFILE\.eos\bin\eos.exe.removing"
  Delete "$INSTDIR\EngineeringOS.cmd"
  Delete "$INSTDIR\uninstall.exe"
  RMDir /r "$INSTDIR\payload"
  RMDir /r "$INSTDIR"
  Delete "$SMPROGRAMS\EngineeringOS\EngineeringOS.lnk"
  Delete "$SMPROGRAMS\EngineeringOS\Uninstall.lnk"
  RMDir "$SMPROGRAMS\EngineeringOS"
  Delete "$DESKTOP\EngineeringOS.lnk"
  DeleteRegKey HKCU "Software\EngineeringOS"
  ClearErrors

  IfSilent uninstall_quiet_done
    Goto uninstall_finished
  uninstall_quiet_done:
    Call un.finish_uninstall
  uninstall_finished:
SectionEnd

Function un.finish_uninstall
  SetErrorLevel 0
  Quit
FunctionEnd
