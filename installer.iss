; PostPilot installer — ships the official, digitally signed Python runtime (Python Software
; Foundation) with the app, so Windows Smart App Control can verify what it runs.
; Build: powershell -ExecutionPolicy Bypass -File build.ps1   (stages build_embed\ then runs ISCC)

#define MyAppName "PostPilot"
#define MyAppVersion "1.2.0"
#define MyAppPublisher "PostPilot"

[Setup]
AppId={{4B8D2E91-6C3A-4F57-B1D4-POSTPILOT001}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppVerName={#MyAppName} {#MyAppVersion}
AppPublisher={#MyAppPublisher}
DefaultDirName={autopf}\{#MyAppName}
DisableProgramGroupPage=yes
LicenseFile=LICENSE.txt
OutputDir=installer_output
OutputBaseFilename=PostPilot-Setup
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
PrivilegesRequired=lowest
ArchitecturesInstallIn64BitMode=x64compatible
ArchitecturesAllowed=x64compatible
SetupIconFile=postpilot.ico
UninstallDisplayIcon={app}\app\postpilot.ico
UninstallDisplayName={#MyAppName} — Social Media Scheduler
CloseApplications=no
VersionInfoVersion={#MyAppVersion}
VersionInfoDescription=PostPilot Setup

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "Create a &desktop shortcut"; Flags: checkedonce

[Files]
Source: "build_embed\runtime\*"; DestDir: "{app}\runtime"; Flags: recursesubdirs createallsubdirs ignoreversion
Source: "build_embed\app\*"; DestDir: "{app}\app"; Flags: recursesubdirs createallsubdirs ignoreversion

[Icons]
Name: "{autoprograms}\{#MyAppName}"; Filename: "{app}\runtime\pythonw.exe"; Parameters: """{app}\app\server.py"""; WorkingDir: "{app}\app"; IconFilename: "{app}\app\postpilot.ico"; Comment: "Schedule posts to all your social networks"
Name: "{userdesktop}\{#MyAppName}"; Filename: "{app}\runtime\pythonw.exe"; Parameters: """{app}\app\server.py"""; WorkingDir: "{app}\app"; IconFilename: "{app}\app\postpilot.ico"; Comment: "Schedule posts to all your social networks"; Tasks: desktopicon

[Run]
Filename: "{app}\runtime\pythonw.exe"; Parameters: """{app}\app\server.py"""; WorkingDir: "{app}\app"; Description: "Launch {#MyAppName}"; Flags: nowait postinstall skipifsilent

[Code]
{ Close a running PostPilot (python/pythonw started from this install folder) so its files can
  be replaced or removed. Only processes whose path is inside the install folder are touched. }
procedure StopPostPilot(Dir: String);
var
  Rc: Integer;
  Cmd: String;
begin
  Cmd := '-NoProfile -ExecutionPolicy Bypass -Command "Get-Process python,pythonw -ErrorAction SilentlyContinue | ' +
         'Where-Object { $_.Path -and $_.Path.ToLower().StartsWith(''' + Lowercase(Dir) + ''') } | ' +
         'Stop-Process -Force -ErrorAction SilentlyContinue; Start-Sleep -Milliseconds 800"';
  Exec(ExpandConstant('{sys}\WindowsPowerShell\v1.0\powershell.exe'), Cmd, '', SW_HIDE, ewWaitUntilTerminated, Rc);
end;

function PrepareToInstall(var NeedsRestart: Boolean): String;
begin
  StopPostPilot(ExpandConstant('{app}'));
  Result := '';
end;

function InitializeUninstall(): Boolean;
begin
  StopPostPilot(ExpandConstant('{app}'));
  Result := True;
end;

[UninstallDelete]
; program files only - your data in %LOCALAPPDATA%\PostPilot (posts, media, accounts) is kept
Type: filesandordirs; Name: "{app}\app"
Type: filesandordirs; Name: "{app}\runtime"
Type: dirifempty; Name: "{app}"
