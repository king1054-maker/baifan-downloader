# 白饭下载器 v0.10.103 · 发布核验清单

> **Private staging：源码已导入，不等于已获准公开或 Windows 版完成验收。**

## 源码公开之前
- [x] 已从用户提供的完整 `github.zip` 导入 v0.10.103 的 57 个 Python 源码文件及必要素材
- [x] 采用 PySide6；保留原 aria2 下载引擎接口以及 BT/磁力、AI 改名、桌宠、背景裁切功能代码
- [x] 16:9 蓝鲸背景和 9:16 作者日本实拍的鸟居红叶背景（1080×1920）进入仓库
- [x] 作者调配音效的两种 Ciallo 选项、默认 `ciallo.wav` 与音量 60% 代码保留
- [x] 版权与许可文件 LICENSE、NOTICE、ASSET_NOTICE、THIRD_PARTY_NOTICES、SECURITY、CONTRIBUTING 均已放入仓库
- [x] 排除历史 EXE/DLL、API Key、个人配置、下载记录、缓存和旧同人背景
- [x] GitHub Actions 语法与素材存在性检查：通过
- [x] 仓库保持 Private；未创建对外 Release
- [ ] 核实两段 `ciallo*.wav` 的原始采样再分发权；当前仓库从一开始即未导入音频文件，避免未来公开后 Git 历史包含未获确认的录音
- [ ] 检查其余图片以及引用的第三方服务、许可与署名是否适合公开发布
- [ ] 审计本地 API（尤其令牌传输）、第三方搜索数据来源与基本隐私风险
- [ ] 所有者确认之后才将仓库改为 Public

**有意不上传的内容**：`ciallo.wav`、`ciallo_alt2.wav`、旧文档界面截图、Windows EXE/安装包以及私有运行数据。保留在原始工程和本地私有测试包，不从历史提交中移除或替换源码逻辑。

## Windows 安装包 Release 前必须单独验收
- [ ] Windows 11 上验证启动、退出、HTTP/BT/磁力、AI 改名、桌宠、音效与横纵背景裁切
- [ ] 根据实际构建物核实 PySide6/Qt、aria2、可能的 FFmpeg 等组件许可证和对应源码义务
- [ ] 基于当前源码重新构建，不重复使用包含旧素材的 EXE
- [ ] 提供 SHA-256、Release Notes、明确安装包平台/限制

**源码托管完成 ≠ 软件构建验证完成 ≠ 公众发布完成。**
