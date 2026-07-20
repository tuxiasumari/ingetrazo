; Inno Setup script for IngeTrazo.
;
; Builds a professional Windows installer: Spanish wizard, GPL license page,
; shortcuts, "Add or Remove Programs" entry and a clean uninstaller.
;
; Local build (needs Inno Setup 6+):
;     iscc /DMyAppVersion=0.2.0 installer\ingetrazo.iss
;
; CI build: see .github/workflows/build-windows.yml
;
; The AppId is a FIXED GUID — never change it between versions, or Windows
; treats every release as a different app and upgrades stop being clean.

#define MyAppName "IngeTrazo"
#define MyAppPublisher "Ing. Marco Sumari Tellez"
#define MyAppURL "https://ingetrazo.com"
#define MyAppExeName "ingetrazo.exe"

; MyAppVersion is injected from the build command with /DMyAppVersion=X.Y.Z
#ifndef MyAppVersion
  #define MyAppVersion "0.0.0-dev"
#endif

[Setup]
; FIXED AppId GUID — generated once for IngeTrazo. Changing it breaks upgrades.
AppId={{63D1C88D-591C-48C8-A13B-5E41810D05E4}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppVerName={#MyAppName} {#MyAppVersion}
AppPublisher={#MyAppPublisher}
AppPublisherURL={#MyAppURL}
AppSupportURL=https://github.com/tuxiasumari/ingetrazo/issues
AppUpdatesURL=https://github.com/tuxiasumari/ingetrazo/releases
VersionInfoVersion={#MyAppVersion}
VersionInfoCompany={#MyAppPublisher}
VersionInfoDescription=Modelador 3D libre para ingenieria y arquitectura
VersionInfoProductName={#MyAppName}

DefaultDirName={autopf}\{#MyAppName}
DefaultGroupName={#MyAppName}
PrivilegesRequired=admin
PrivilegesRequiredOverridesAllowed=dialog

; Free software: the "license" page shows the GPL-3.0 text.
LicenseFile=..\LICENSE

OutputDir=..\dist
OutputBaseFilename=ingetrazo-setup-v{#MyAppVersion}

WizardStyle=modern
ShowLanguageDialog=no
DisableProgramGroupPage=yes
; We register file-type icons/associations — tells Explorer to refresh them.
ChangesAssociations=yes
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
Compression=lzma2
SolidCompression=yes
UninstallDisplayIcon={app}\{#MyAppExeName}
SetupIconFile=..\resources\icons\ingetrazo.ico

[Languages]
Name: "spanish"; MessagesFile: "compiler:Languages\Spanish.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; \
    GroupDescription: "{cm:AdditionalIcons}"; Flags: unchecked

[Files]
; The whole PyInstaller one-dir bundle.
Source: "..\dist\ingetrazo\*"; DestDir: "{app}"; \
    Flags: ignoreversion recursesubdirs createallsubdirs
; Branded document icons, copied to the {app} root so [Registry] DefaultIcon
; entries can reference them with a stable path.
Source: "..\resources\icons\mimetypes\ingetrazo-igz.ico"; DestDir: "{app}"; \
    Flags: ignoreversion
Source: "..\resources\icons\mimetypes\ingetrazo-dae.ico"; DestDir: "{app}"; \
    Flags: ignoreversion
Source: "..\resources\icons\mimetypes\ingetrazo-skp.ico"; DestDir: "{app}"; \
    Flags: ignoreversion

[Icons]
Name: "{autoprograms}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; \
    Tasks: desktopicon

[Registry]
; ── .igz — IngeTrazo's own format: full association + branded document icon ──
; Double-click opens the document in IngeTrazo and Explorer shows the .igz icon.
Root: HKA; Subkey: "Software\Classes\.igz"; ValueType: string; \
    ValueData: "IngeTrazo.Document"; Flags: uninsdeletevalue
Root: HKA; Subkey: "Software\Classes\IngeTrazo.Document"; ValueType: string; \
    ValueData: "Documento de IngeTrazo"; Flags: uninsdeletekey
Root: HKA; Subkey: "Software\Classes\IngeTrazo.Document\DefaultIcon"; \
    ValueType: string; ValueData: "{app}\ingetrazo-igz.ico,0"
Root: HKA; Subkey: "Software\Classes\IngeTrazo.Document\shell\open\command"; \
    ValueType: string; ValueData: """{app}\{#MyAppExeName}"" ""%1"""

; ── .dae / .skp — standard interchange formats: "Open with" only ────────────
; We DO NOT take over the default program or the file icon (Windows ties the
; shown icon to the default handler, and stealing .dae/.skp from Blender/
; SketchUp would surprise the user). Instead we register a ProgId and add it to
; each extension's OpenWithProgids list, so IngeTrazo appears in the right-click
; "Open with" menu. The ProgId carries the branded icon, which only takes visual
; effect if the user later chooses IngeTrazo as the default for these files.
Root: HKA; Subkey: "Software\Classes\IngeTrazo.dae"; ValueType: string; \
    ValueData: "Modelo COLLADA (IngeTrazo)"; Flags: uninsdeletekey
Root: HKA; Subkey: "Software\Classes\IngeTrazo.dae\DefaultIcon"; \
    ValueType: string; ValueData: "{app}\ingetrazo-dae.ico,0"
Root: HKA; Subkey: "Software\Classes\IngeTrazo.dae\shell\open\command"; \
    ValueType: string; ValueData: """{app}\{#MyAppExeName}"" ""%1"""
Root: HKA; Subkey: "Software\Classes\.dae\OpenWithProgids"; \
    ValueType: string; ValueName: "IngeTrazo.dae"; ValueData: ""; \
    Flags: uninsdeletevalue

Root: HKA; Subkey: "Software\Classes\IngeTrazo.skp"; ValueType: string; \
    ValueData: "Modelo de SketchUp (IngeTrazo)"; Flags: uninsdeletekey
Root: HKA; Subkey: "Software\Classes\IngeTrazo.skp\DefaultIcon"; \
    ValueType: string; ValueData: "{app}\ingetrazo-skp.ico,0"
Root: HKA; Subkey: "Software\Classes\IngeTrazo.skp\shell\open\command"; \
    ValueType: string; ValueData: """{app}\{#MyAppExeName}"" ""%1"""
Root: HKA; Subkey: "Software\Classes\.skp\OpenWithProgids"; \
    ValueType: string; ValueName: "IngeTrazo.skp"; ValueData: ""; \
    Flags: uninsdeletevalue

[Run]
Filename: "{app}\{#MyAppExeName}"; \
    Description: "{cm:LaunchProgram,{#MyAppName}}"; \
    Flags: nowait postinstall skipifsilent
