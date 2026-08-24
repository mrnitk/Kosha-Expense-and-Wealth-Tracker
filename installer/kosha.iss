; Inno Setup script for Kosha — Windows installer.
;
; Prerequisites:
;   1. Build the app first:  python -m PyInstaller --noconfirm kosha.spec
;      (produces dist\Kosha\ — a onedir bundle)
;   2. Install Inno Setup 6:  https://jrsoftware.org/isdl.php
;   3. Compile:  "C:\Program Files (x86)\Inno Setup 6\ISCC.exe" installer\kosha.iss
;      -> produces installer\Output\KoshaSetup-<version>.exe
;
; Kosha is fully local: it stores its encrypted vault in %APPDATA%\Kosha and
; never touches the network. The installer only lays down the program files.

#define AppName "Kosha"
#define AppVersion "4.1.0"
#define AppPublisher "Kosha"
#define AppExeName "Kosha.exe"

[Setup]
AppId={{A6E3F1C2-9B4D-4E7A-8C21-0F5D6A7B8C90}
AppName={#AppName}
AppVersion={#AppVersion}
AppPublisher={#AppPublisher}
DefaultDirName={autopf}\{#AppName}
DefaultGroupName={#AppName}
DisableProgramGroupPage=yes
; Per-user install by default (no admin needed); vault lives in %APPDATA%.
PrivilegesRequiredOverridesAllowed=dialog
OutputDir=Output
OutputBaseFilename=KoshaSetup-{#AppVersion}
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
UninstallDisplayIcon={app}\{#AppExeName}

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"; Flags: unchecked

[Files]
; The entire PyInstaller onedir output. "*" + recursesubdirs pulls in the whole
; tree (Qt plugins, QtWebEngine resources, sqlcipher DLL, plotly data, etc.).
Source: "..\dist\Kosha\{#AppExeName}"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\dist\Kosha\*";             DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\{#AppName}"; Filename: "{app}\{#AppExeName}"
Name: "{group}\Uninstall {#AppName}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\{#AppExeName}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#AppExeName}"; Description: "{cm:LaunchProgram,{#AppName}}"; Flags: nowait postinstall skipifsilent

[UninstallDelete]
; Program files only. The user's encrypted vault in %APPDATA%\Kosha is left in
; place on purpose — uninstalling the app must never destroy their data.
Type: filesandordirs; Name: "{app}"
