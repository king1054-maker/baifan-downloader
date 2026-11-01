; 白饭下载器 —— Inno Setup 安装脚本
; 装到 %LOCALAPPDATA%\Programs\BaiFanDownloader，全程不需要管理员权限。
; 编译：packaging\build.py 会自动调用 ISCC.exe 编译本文件。

#define AppName        "白饭下载器"
#define AppNameEn      "BaiFan Downloader"
#define AppVersion     "0.10.103"
#define AppPublisher   "白饭下载器"
#define AppExe         "BaiFanDownloader.exe"
#define AppId          "{{8F3A1C2E-5B7D-4E9A-9C11-B1A2F3D4E5F6}"

[Setup]
AppId={#AppId}
AppName={#AppName}
AppVersion={#AppVersion}
AppVerName={#AppName} {#AppVersion}
AppPublisher={#AppPublisher}
VersionInfoVersion={#AppVersion}
VersionInfoDescription={#AppName} 安装程序
DefaultDirName={localappdata}\Programs\BaiFanDownloader
DefaultGroupName={#AppName}
DisableProgramGroupPage=yes
UninstallDisplayName={#AppName}
UninstallDisplayIcon={app}\{#AppExe}
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
OutputDir=..\dist
OutputBaseFilename=白饭下载器-{#AppVersion}-安装包
SetupIconFile=..\assets\icons\whale.ico
WizardStyle=modern
Compression=lzma2/max
SolidCompression=yes
LZMANumBlockThreads=4
CloseApplications=yes
RestartApplications=no
AllowNoIcons=yes
MinVersion=10.0

[Languages]
Name: "chinesesimplified"; MessagesFile: "ChineseSimplified.isl"
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "在桌面放一个「{#AppName}」"; GroupDescription: "快捷方式："; Flags: checkedonce
Name: "torrentassoc"; Description: "用「{#AppName}」打开 .torrent 种子文件"; GroupDescription: "文件关联："; Flags: checkedonce
Name: "startup";      Description: "开机自动启动（常驻托盘，随时接链接）"; GroupDescription: "启动项："; Flags: unchecked

[Files]
; 整个 onedir 产物原样铺进去（含 _internal、assets、bin\aria2c.exe）
Source: "..\dist\BaiFanDownloader\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs
; 装完先给一个空的下载目录，省得用户第一次找不到东西放哪
Source: "..\packaging\Download.keep"; DestDir: "{app}\Download"; Flags: ignoreversion

[Icons]
Name: "{group}\{#AppName}"; Filename: "{app}\{#AppExe}"; IconFilename: "{app}\{#AppExe}"; Comment: "{#AppName} · 一碗一碗，喂饱大肥鱼"
Name: "{group}\下载文件夹"; Filename: "{app}\Download"
Name: "{group}\卸载 {#AppName}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\{#AppExe}"; IconFilename: "{app}\{#AppExe}"; Tasks: desktopicon

[Registry]
; .torrent 关联（只写 HKCU，不影响系统里其它下载器对别的扩展名的关联）
Root: HKCU; Subkey: "Software\Classes\.torrent"; ValueType: string; ValueName: ""; ValueData: "BaiFan.Torrent"; Flags: uninsdeletevalue; Tasks: torrentassoc
Root: HKCU; Subkey: "Software\Classes\BaiFan.Torrent"; ValueType: string; ValueName: ""; ValueData: "BitTorrent 种子文件"; Flags: uninsdeletekey; Tasks: torrentassoc
Root: HKCU; Subkey: "Software\Classes\BaiFan.Torrent"; ValueType: string; ValueName: "FriendlyTypeName"; ValueData: "种子文件"; Tasks: torrentassoc
Root: HKCU; Subkey: "Software\Classes\BaiFan.Torrent\DefaultIcon"; ValueType: string; ValueName: ""; ValueData: "{app}\{#AppExe},0"; Tasks: torrentassoc
Root: HKCU; Subkey: "Software\Classes\BaiFan.Torrent\shell\open\command"; ValueType: string; ValueName: ""; ValueData: """{app}\{#AppExe}"" ""%1"""; Tasks: torrentassoc

; 程序自己列的「默认程序」条目，右键→打开方式里能直接看到名字和图标
Root: HKCU; Subkey: "Software\Classes\Applications\{#AppExe}"; ValueType: string; ValueName: "FriendlyAppName"; ValueData: "{#AppName}"; Flags: uninsdeletekey
Root: HKCU; Subkey: "Software\Classes\Applications\{#AppExe}\shell\open\command"; ValueType: string; ValueName: ""; ValueData: """{app}\{#AppExe}"" ""%1"""
Root: HKCU; Subkey: "Software\Classes\Applications\{#AppExe}\DefaultIcon"; ValueType: string; ValueName: ""; ValueData: "{app}\{#AppExe},0"

Root: HKCU; Subkey: "Software\Microsoft\Windows\CurrentVersion\Run"; ValueType: string; ValueName: "BaiFanDownloader"; ValueData: """{app}\{#AppExe}"""; Flags: uninsdeletevalue; Tasks: startup

[Run]
Filename: "{app}\{#AppExe}"; Description: "立刻开饭（启动 {#AppName}）"; Flags: nowait postinstall skipifsilent

[UninstallDelete]
; 只清理运行期产生的缓存/日志，**不动 Download**（用户的片子不能删）
Type: filesandordirs; Name: "{app}\logs"
Type: filesandordirs; Name: "{app}\_internal\__pycache__"
Type: files; Name: "{app}\conf\session"
Type: files; Name: "{app}\data\.cache"
