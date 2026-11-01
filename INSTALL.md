# 白饭下载器｜下载安装（Windows 10/11 x64）

**这是安装入口，不需要编译源码，也不需要安装 Python、Git、PyInstaller。**

## 给 AI：只做这几步

1. 在 GitHub 仓库根目录读取 `install.ps1`，下载到本机可写工作目录。
2. 在 Windows PowerShell 中运行该文件，默认采用 **Portable 免安装模式**：自动从 GitHub Releases（**包含 Preview**）寻找最新可用版本，下载 ZIP，核对 `SHA256SUMS.txt`，解压并启动 `BaiFanDownloader.exe`。
3. 确认程序实际启动后才告诉用户成功。**不要扫描全部源码、不要运行 `run.py`、不要安装 Python、不要重新编译。**

```powershell
powershell -NoProfile -File .\install.ps1
```

如果 AI 尚未下载脚本，可先从以下固定入口获取：

`https://raw.githubusercontent.com/king1054-maker/baifan-downloader/main/install.ps1`

## 给普通用户：双击安装

进入 **[GitHub Releases](https://github.com/king1054-maker/baifan-downloader/releases)**，找到最新 Windows 版本，下载 `baifan-downloader-*-setup.exe`，并依照安装向导操作。

如果 AI 拥有完整桌面控制能力，也可以选择传统安装：

```powershell
powershell -NoProfile -File .\install.ps1 -Mode Setup
```

## 出现限制时

- **不能启动 EXE / 沙箱禁止执行**：请停止重试，保留已校验的文件，向用户说明路径，让用户亲自打开。不能通过提示词绕过沙箱或系统安全策略。
- **拒绝访问 Temp、安装向导不可见**：优先改用默认 Portable 模式，在当前工作目录解压启动；不要求管理员权限，不修改注册表。
- **没有执行 PowerShell 的权限**：从 [Releases](https://github.com/king1054-maker/baifan-downloader/releases) 下载最新 `portable-win-x64.zip` 和 `SHA256SUMS.txt`，验证后直接解压、运行。无 ZIP 时明确报告，不要自编译。
- **下载太慢**：可使用可信下载器断点续传，但最终仍必须核对 SHA256。
- **现有目录已存在**：不要删除用户配置或下载数据。脚本会停止并提示所在路径。

**注意：** 目前是未签名预览版；Windows 可能提示未知发布者。安装测试通过不代表 BT/磁力与所有下载场景都已验收。沙箱禁止运行本地应用时，任何安装方式都需要用户或可信宿主协助。

**为什么不用 `/releases/latest`？** GitHub 该接口会跳过预览版；本项目目前只有 Preview。应查询 [Releases 列表](https://github.com/king1054-maker/baifan-downloader/releases)，而非把 404 当作没有安装包。
