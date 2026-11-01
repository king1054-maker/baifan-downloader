> **历史存档：这是旧工程导入／预发布记录，不是当前安装说明。** 要下载白饭下载器，请返回 [INSTALL.md](../../INSTALL.md) 或 [Releases](https://github.com/king1054-maker/baifan-downloader/releases)。

---

# 把已有白饭下载器工程导入本私有仓库

**当前仓库状态：Private。** 必须从已有的 **v0.10.103 / PySide6** 工程导入完整源码。不要重新搭建、生成一个空壳工程、或者覆盖新功能。

仓库：`https://github.com/king1054-maker/baifan-downloader`

## 给负责开发的 AI（在拥有原始工程的 Windows 11 电脑上执行）

1. 在本机确认拥有全部源文件：`run.py`、`app/`、`assets/`、`tools/`、`packaging/`、`requirements.txt`。先备份完整工程。
2. 拉取本私有仓库（需要使用仓库所有者的 GitHub 身份进行**正常登录授权**；不要申请不相关软件的 OAuth 权限）：

   ```powershell
   git clone https://github.com/king1054-maker/baifan-downloader.git
   cd baifan-downloader
   ```

3. 从本机已有完整工程复制 `app/`、`run.py`、`assets/`、`tools/`、`packaging/`、`docs/`、`requirements.txt`、`requirements-build.txt` 等源码和必要资源进入仓库。**先比较已有 LICENSE/README/NOTICE，不要覆盖已经提交的仓库发布说明。** 若缺少 Git，开发 AI 可使用本机其他已经授权的 GitHub 上传方式。
4. 不要提交 `*.exe`、`*.dll`、旧安装包/绿色版、私人配置/密钥、下载记录、缓存、旧《原神》背景素材。原 `bin/aria2c.exe` 保存在本机测试工程，不应作为本仓库代码的一部分。
5. 两张示例壁纸：横图为蓝鲸主题 16:9；竖图为作者**日本实拍的完整红叶鸟居、包含参道的第一张照片**，居中裁切 1080×1920，最终写入 `assets/backgrounds/默认竖图-示例.jpg`。
6. 作者混音提示音 `ciallo.wav` 与 `ciallo_alt2.wav` 在**本机私有开发版**必须保留，`ciallo.wav` 默认播放、音量 60%。由于公开分发原始录音素材的再授权尚待核验，导入 GitHub **前请先排除这两段音频**；保留调用逻辑，缺失时允许用户自行设置，不能将其替换为水波声。如权利明确后，可以再补充提交。这是为了避免今后变更为 Public 时意外公开未核实的原始采样。
7. 跑基础检查：

   ```powershell
   python -m compileall -q app run.py tools packaging
   git status --short
   ```

8. 确认没有密钥和不该公开的媒体文件，确认实际软件文件已复制。随后提交和推送：

   ```powershell
   git add .
   git commit -m "feat: import BaiFan Downloader v0.10.103 source"
   git push origin main
   ```

9. 记录 GitHub 上的提交链接和测试结果，到 Issue #1 评论。若出现冲突，**停止而不要 force push**。

## 后续公开步骤

先由 ChatGPT 在 GitHub 私有仓库审查程序文件、依赖和素材，再经作者确认转换为 Public。Windows 安装包/EXE 发行需另行检验，不要用历史构建包直接上线。

商业授权联系邮箱：meetmusk@icloud.com。
