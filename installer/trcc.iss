; TRCC Windows Installer — Inno Setup Script
; Built by GitHub Actions, not manually.
;
; Receives APP_VERSION via /D flag from CI:
;   iscc /DAPP_VERSION=8.3.10 trcc.iss

#ifndef APP_VERSION
  #define APP_VERSION "0.0.0"
#endif

[Setup]
AppId={{F7A3C2E1-8B4D-4F9A-B6E2-1D3C5A7F8E90}
AppName=TRCC
AppVersion={#APP_VERSION}
AppVerName=TRCC {#APP_VERSION}
AppPublisher=TRCC Linux Contributors
AppPublisherURL=https://github.com/Lexonight1/thermalright-trcc-linux
AppSupportURL=https://github.com/Lexonight1/thermalright-trcc-linux/issues
DefaultDirName={autopf}\TRCC
DefaultGroupName=TRCC
OutputDir=Output
OutputBaseFilename=trcc-{#APP_VERSION}-setup
SetupIconFile=..\src\trcc\assets\icons\app.ico
UninstallDisplayIcon={app}\trcc-gui.exe
Compression=lzma2
SolidCompression=yes
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
WizardStyle=modern
PrivilegesRequired=admin
; The [Registry] PATH entry is a machine-wide environment change.  Without this
; Setup never broadcasts WM_SETTINGCHANGE, so Explorer -- and every terminal it
; opens -- keeps the old PATH and 'trcc' is "not recognized" until the user
; signs out (#218).  Applies to the uninstall's PATH removal as well.
ChangesEnvironment=yes
LicenseFile=..\LICENSE
; Upgrade: detect running TRCC and offer to close it
CloseApplications=yes
CloseApplicationsFilter=trcc*.exe

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"
Name: "quicklaunchicon"; Description: "{cm:CreateQuickLaunchIcon}"; GroupDescription: "{cm:AdditionalIcons}"; Flags: unchecked

[InstallDelete]
; Clean stale PyInstaller files from previous version before installing new ones
Type: filesandordirs; Name: "{app}\_internal"
Type: files; Name: "{app}\trcc.exe"
Type: files; Name: "{app}\trcc-gui.exe"
; LibreHardwareMonitor, which older versions shipped and launched; 0.9.5 dropped
; the WMI TRCC read it through, so nothing uses it now (#319)
Type: filesandordirs; Name: "{app}\lhm"

[Files]
; PyInstaller output directory (all DLLs, Python runtime, Qt, app code)
Source: "..\dist\trcc\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs
; WinUSB driver .inf kept for reference — not auto-installed (requires signed cert)
; Users needing WinUSB (bulk/HID devices) are guided to Zadig via `trcc doctor`
Source: "trcc-usb.inf"; DestDir: "{app}\driver"; Flags: ignoreversion
; PawnIO (namazso, https://pawnio.eu): the signed driver TRCC reads CPU
; temperature and power through (#319).  Downloaded and hash-checked by
; windows.yml; run below, then deleted.  Redistribution of its installer is
; allowed.
Source: "pawnio\PawnIO_setup.exe"; DestDir: "{tmp}"; Flags: deleteafterinstall

[Icons]
Name: "{group}\TRCC"; Filename: "{app}\trcc-gui.exe"; Comment: "Thermalright LCD Control Center"
Name: "{group}\TRCC (CLI)"; Filename: "{app}\trcc.exe"; Comment: "TRCC Command Line"
Name: "{group}\{cm:UninstallProgram,TRCC}"; Filename: "{uninstallexe}"
Name: "{commondesktop}\TRCC"; Filename: "{app}\trcc-gui.exe"; Tasks: desktopicon
Name: "{userappdata}\Microsoft\Internet Explorer\Quick Launch\TRCC"; Filename: "{app}\trcc-gui.exe"; Tasks: quicklaunchicon

[Run]
; PawnIO, silently, unless it is already there (another app may have put it).
; Without it Windows gives TRCC no CPU temperature or power.  It is shared,
; so TRCC's uninstaller leaves it; it uninstalls from Windows' Apps list.
Filename: "{tmp}\PawnIO_setup.exe"; Parameters: "-install -silent"; \
    StatusMsg: "Installing PawnIO (CPU temperature and power)..."; \
    Flags: waituntilterminated; Check: not PawnIOInstalled
; TRCC's Windows service (TRCCService), as SYSTEM: drives SCSI panels and
; reads PawnIO for an App that is not administrator (win_service.py).  No
; extra exe -- Windows runs "trcc-gui.exe service".  create fails harmlessly
; on an upgrade, where config points the existing service at this install.
; A crash restarts it after 5 s.
Filename: "{sys}\sc.exe"; Parameters: "create TRCCService binPath= ""\""{app}\trcc-gui.exe\"" service"" start= auto DisplayName= ""TRCC hardware access"""; \
    StatusMsg: "Installing the TRCC service..."; Flags: runhidden waituntilterminated
Filename: "{sys}\sc.exe"; Parameters: "config TRCCService binPath= ""\""{app}\trcc-gui.exe\"" service"" start= auto"; \
    Flags: runhidden waituntilterminated
Filename: "{sys}\sc.exe"; Parameters: "description TRCCService ""Lets TRCC drive SCSI LCD panels and read CPU temperature without running as administrator."""; \
    Flags: runhidden waituntilterminated
Filename: "{sys}\sc.exe"; Parameters: "failure TRCCService reset= 86400 actions= restart/5000"; \
    Flags: runhidden waituntilterminated
Filename: "{sys}\sc.exe"; Parameters: "start TRCCService"; Flags: runhidden waituntilterminated
; WinUSB driver not auto-installed (requires signed certificate).
; Users with bulk/HID devices are guided to Zadig via `trcc doctor`.
Filename: "{app}\trcc-gui.exe"; Description: "{cm:LaunchProgram,TRCC}"; Flags: nowait postinstall skipifsilent shellexec

[Registry]
; Add trcc CLI to PATH
Root: HKLM; Subkey: "SYSTEM\CurrentControlSet\Control\Session Manager\Environment"; \
    ValueType: expandsz; ValueName: "Path"; ValueData: "{olddata};{app}"; \
    Check: NeedsAddPath('{app}')

[UninstallDelete]
; The app directory.  (Autostart is removed in CurUninstallStepChanged: the
; sign-in task and the Run values.  Nothing ever wrote to the Startup folder.)
Type: filesandordirs; Name: "{app}"
; TRCC's own config and data: %APPDATA%\trcc (WindowsPaths), and the ~/.trcc
; versions before it used.  Only ~/.trcc was named here until 2026-10-16, so an
; uninstall left %APPDATA%\trcc behind.  %LOCALAPPDATA%\trcc-user is KEPT: it
; holds the themes and backgrounds the user made.
Type: filesandordirs; Name: "{userappdata}\trcc"
Type: filesandordirs; Name: "{%USERPROFILE}\.trcc"

[UninstallRun]
; The service first, cleanly (net stop waits for it), then gone -- it runs as
; trcc-gui.exe, so the taskkill below would otherwise end it mid-request.
Filename: "{sys}\net.exe"; Parameters: "stop TRCCService"; Flags: runhidden waituntilterminated; RunOnceId: "StopService"
Filename: "{sys}\sc.exe"; Parameters: "delete TRCCService"; Flags: runhidden waituntilterminated; RunOnceId: "DeleteService"
; Kill TRCC processes before uninstall
Filename: "{sys}\taskkill.exe"; Parameters: "/F /IM trcc-gui.exe"; Flags: runhidden; RunOnceId: "KillGUI"
Filename: "{sys}\taskkill.exe"; Parameters: "/F /IM trcc.exe"; Flags: runhidden; RunOnceId: "KillCLI"

[Code]
function PawnIOInstalled: Boolean;
begin
  { PawnIO's own installer registers this key; LibreHardwareMonitor checks it too }
  Result := RegKeyExists(HKLM, 'SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall\PawnIO');
end;

procedure KillRunningProcesses;
var
  ResultCode: Integer;
begin
  // TRCC's service runs as trcc-gui.exe and holds the files an upgrade
  // replaces: stop it cleanly first (net stop waits); [Run] starts it again.
  Exec(ExpandConstant('{sys}\net.exe'), 'stop TRCCService', '',
       SW_HIDE, ewWaitUntilTerminated, ResultCode);
  Exec(ExpandConstant('{sys}\taskkill.exe'), '/F /IM trcc-gui.exe', '',
       SW_HIDE, ewWaitUntilTerminated, ResultCode);
  Exec(ExpandConstant('{sys}\taskkill.exe'), '/F /IM trcc.exe', '',
       SW_HIDE, ewWaitUntilTerminated, ResultCode);
  // The LibreHardwareMonitor an older TRCC launched from its lhm folder: the
  // taskkill above gives TRCC no chance to stop it, so it lives on orphaned,
  // holding the folder [InstallDelete] removes (seen in the VM 2026-10-16).
  // Matched by path, so an LHM the user runs from anywhere else is left alone.
  Exec(ExpandConstant('{sys}\WindowsPowerShell\v1.0\powershell.exe'),
       '-NoProfile -Command "Get-Process LibreHardwareMonitor -ErrorAction ' +
       'SilentlyContinue | Where-Object { $_.Path -like ''' +
       ExpandConstant('{app}') + '\lhm\*'' } | Stop-Process -Force"',
       '', SW_HIDE, ewWaitUntilTerminated, ResultCode);
end;

function PrepareToInstall(var NeedsRestart: Boolean): String;
begin
  KillRunningProcesses;
  Result := '';
end;

function NeedsAddPath(Param: string): Boolean;
var
  OrigPath: string;
begin
  if not RegQueryStringValue(HKEY_LOCAL_MACHINE,
    'SYSTEM\CurrentControlSet\Control\Session Manager\Environment',
    'Path', OrigPath)
  then begin
    Result := True;
    exit;
  end;
  Result := Pos(';' + UpperCase(Param) + ';', ';' + UpperCase(OrigPath) + ';') = 0;
end;

procedure RemoveFromPath(AppPath: string);
var
  OrigPath: string;
  NewPath: string;
  P: Integer;
begin
  if not RegQueryStringValue(HKEY_LOCAL_MACHINE,
    'SYSTEM\CurrentControlSet\Control\Session Manager\Environment',
    'Path', OrigPath)
  then exit;

  { Remove ;AppPath from path — handles both middle and trailing positions }
  NewPath := OrigPath;
  P := Pos(';' + UpperCase(AppPath), UpperCase(NewPath));
  if P > 0 then
    Delete(NewPath, P, 1 + Length(AppPath));

  if NewPath <> OrigPath then
    RegWriteExpandStringValue(HKEY_LOCAL_MACHINE,
      'SYSTEM\CurrentControlSet\Control\Session Manager\Environment',
      'Path', NewPath);
end;

procedure CurStepChanged(CurStep: TSetupStep);
var
  ResultCode: Integer;
begin
  { The elevated builds before TRCC's service autostarted from a sign-in task
    at the highest run level.  This build is not elevated and autostarts from
    the Run key, so the task is replaced: first the App carries its target
    over, AS THE USER (it writes their HKCU, and reads the task's arguments,
    whose format lives in _autostart.py only), then the task goes, as admin --
    a normal process may not delete a task made elevated (measured on the
    win11 VM, 2026-10-19).  TRCC_DAEMON=0 keeps the App in this one process:
    no background App is left running by the installer.  The task name must
    match src/trcc/adapters/system/_autostart.py -- gated by
    tests/test_installer_uninstall.py. }
  if CurStep <> ssPostInstall then
    exit;
  if not Exec(ExpandConstant('{sys}\schtasks.exe'), '/query /tn "TRCC Linux"',
              '', SW_HIDE, ewWaitUntilTerminated, ResultCode)
     or (ResultCode <> 0) then
    exit;
  ExecAsOriginalUser(ExpandConstant('{cmd}'),
    '/S /C "set TRCC_DAEMON=0&& "' + ExpandConstant('{app}\trcc.exe') +
    '" system autostart refresh"',
    '', SW_HIDE, ewWaitUntilTerminated, ResultCode);
  Log(Format('autostart refresh as the user exited %d', [ResultCode]));
  Exec(ExpandConstant('{sys}\schtasks.exe'), '/delete /tn "TRCC Linux" /f',
       '', SW_HIDE, ewWaitUntilTerminated, ResultCode);
  Log(Format('old sign-in task delete exited %d', [ResultCode]));
end;

procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
var
  ResultCode: Integer;
begin
  if CurUninstallStep = usUninstall then
  begin
    { Remove TRCC from system PATH }
    RemoveFromPath(ExpandConstant('{app}'));

    { Remove every autostart TRCC may have set.  The names are identity and
      must match src/trcc/adapters/system/_autostart.py -- gated by
      tests/test_installer_uninstall.py.  The sign-in task (_TASK_NAME) is
      what the elevated builds used, gone after any later install; kept here
      for an uninstall straight from one -- left behind, it runs a deleted
      program at every sign-in. }
    Exec(ExpandConstant('{sys}\schtasks.exe'), '/delete /tn "TRCC Linux" /f',
         '', SW_HIDE, ewWaitUntilTerminated, ResultCode);
    RegDeleteValue(HKEY_CURRENT_USER,
      'Software\Microsoft\Windows\CurrentVersion\Run',
      'TRCCNext');
    RegDeleteValue(HKEY_CURRENT_USER,
      'Software\Microsoft\Windows\CurrentVersion\Run',
      'TRCC Linux');
  end;
end;
