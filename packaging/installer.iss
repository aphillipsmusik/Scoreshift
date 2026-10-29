; Inno Setup script for ScoreShift.
; Compile with:  ISCC.exe packaging\installer.iss
; (build.ps1 does this for you after PyInstaller has run.)

#define AppName      "ScoreShift"
#define AppVersion   "1.9"
#define AppPublisher "Austin Phillips"
#define AppExe       "ScoreShift.exe"

[Setup]
AppId={{7C4C1A2E-4E7B-4C2E-9F3B-5A9D2B7E1C40}
AppName={#AppName}
AppVersion={#AppVersion}
AppVerName={#AppName} {#AppVersion}
AppPublisher={#AppPublisher}
DefaultDirName={autopf}\{#AppName}
DefaultGroupName={#AppName}
DisableProgramGroupPage=yes
OutputDir=..\dist
OutputBaseFilename=ScoreShift-Setup-{#AppVersion}
SetupIconFile=..\assets\scoreshift.ico
UninstallDisplayIcon={app}\{#AppExe}
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
ArchitecturesInstallIn64BitMode=x64compatible
ArchitecturesAllowed=x64compatible
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog commandline
DisableDirPage=no
LicenseFile=
InfoBeforeFile=

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "Create a desktop shortcut"; \
    GroupDescription: "Shortcuts:"
Name: "installengine"; Description: \
    "Install the note-reading engine (Audiveris) and Java if they are missing"; \
    GroupDescription: "Required components:"
Name: "fileassoc"; Description: \
    "Open MusicXML files with ScoreShift"; \
    GroupDescription: "Optional:"; Flags: unchecked

[Files]
Source: "..\dist\ScoreShift\{#AppExe}"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\dist\ScoreShift\*"; DestDir: "{app}"; \
    Flags: ignoreversion recursesubdirs createallsubdirs
Source: "..\packaging\install-deps.ps1"; DestDir: "{app}\tools"; Flags: ignoreversion
Source: "..\README.md"; DestDir: "{app}"; Flags: ignoreversion isreadme
Source: "..\PORTING-iOS.md"; DestDir: "{app}"; Flags: ignoreversion skipifsourcedoesntexist

[Icons]
Name: "{group}\{#AppName}"; Filename: "{app}\{#AppExe}"
Name: "{group}\{#AppName} API server"; Filename: "{app}\{#AppExe}"; \
    Parameters: "--server --host 0.0.0.0 --port 8765"; \
    Comment: "Serve the engine to a phone or another computer"
Name: "{group}\Uninstall {#AppName}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\{#AppExe}"; Tasks: desktopicon

[Registry]
Root: HKCU; Subkey: "Software\Classes\.musicxml"; ValueType: string; \
    ValueName: ""; ValueData: "ScoreShift.Score"; Flags: uninsdeletevalue; Tasks: fileassoc
Root: HKCU; Subkey: "Software\Classes\.mxl"; ValueType: string; \
    ValueName: ""; ValueData: "ScoreShift.Score"; Flags: uninsdeletevalue; Tasks: fileassoc
Root: HKCU; Subkey: "Software\Classes\ScoreShift.Score"; ValueType: string; \
    ValueName: ""; ValueData: "Sheet music"; Flags: uninsdeletekey; Tasks: fileassoc
Root: HKCU; Subkey: "Software\Classes\ScoreShift.Score\DefaultIcon"; ValueType: string; \
    ValueName: ""; ValueData: "{app}\{#AppExe},0"; Tasks: fileassoc
Root: HKCU; Subkey: "Software\Classes\ScoreShift.Score\shell\open\command"; \
    ValueType: string; ValueName: ""; ValueData: """{app}\{#AppExe}"" ""%1"""; Tasks: fileassoc

[Run]
Filename: "powershell.exe"; \
    Parameters: "-ExecutionPolicy Bypass -NoProfile -File ""{app}\tools\install-deps.ps1"""; \
    StatusMsg: "Installing Audiveris - this downloads about 300 MB and can take a few minutes..."; \
    Flags: waituntilterminated; Tasks: installengine
Filename: "{app}\{#AppExe}"; Description: "Start {#AppName} now"; \
    Flags: nowait postinstall skipifsilent

[UninstallDelete]
Type: filesandordirs; Name: "{localappdata}\ScoreShift\work"
