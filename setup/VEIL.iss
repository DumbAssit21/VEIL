#define AppVersion "0.10.0-beta.2"
#define AppName "VEIL"
#define AppPublisher "VEIL"

[Setup]
AppId={{5F32045D-9538-42AB-8DF6-1A23ED64C6BB}
AppName={#AppName}
AppVersion={#AppVersion}
AppVerName=VEIL {#AppVersion}
AppPublisher={#AppPublisher}
AppPublisherURL=https://github.com/DumbAssit21/VEIL
AppSupportURL=https://github.com/DumbAssit21/VEIL/issues
DefaultDirName={localappdata}\Programs\VEIL
DefaultGroupName=VEIL
UninstallDisplayIcon={app}\VEIL.exe
OutputDir=..\release
OutputBaseFilename=VEIL-Setup-v{#AppVersion}
SetupIconFile=..\assets\VEIL.ico
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
PrivilegesRequired=lowest
CloseApplications=yes
RestartApplications=no
ArchitecturesAllowed=x64compatible

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "Create a desktop shortcut"; GroupDescription: "Additional shortcuts:"; Flags: unchecked

[Files]
Source: "..\dist\VEIL\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\VEIL"; Filename: "{app}\VEIL.exe"
Name: "{autodesktop}\VEIL"; Filename: "{app}\VEIL.exe"; Tasks: desktopicon

[Run]
Filename: "{app}\VEIL.exe"; Description: "Launch VEIL"; Flags: nowait postinstall skipifsilent

; Intentionally no [UninstallDelete] paths touching user-selected vault drives.
