# 发布核验清单 / Release Gates

> 本仓库当前为 Private，代码和素材尚未完整导入。

## 源码公开之前
- [ ] 完整上传 v0.10.103 项目源码，保留原有下载、BT/磁力、AI 改名、桌宠和背景裁切功能
- [ ] 确认 PySide6 导入；不重新开发 aria2 内核
- [ ] 16:9：蓝鲸原创横版演示背景；9:16：作者日本实拍鸟居红叶照片居中裁切（1080×1920）
- [ ] 两段作者调配音效（ciallo.wav、ciallo_alt2.wav）在私有工程中完整保留；默认 ciallo.wav、音量 60%
- [ ] 原始采样/配音再分发许可核实；若不明确，公开包排除音频文件、保留代码内兼容和用户自定义能力
- [ ] README、LICENSE、NOTICE、ASSET_NOTICE、THIRD_PARTY_NOTICES、SECURITY 均已导入并相互一致
- [ ] 排除 API Key、访问令牌、个人配置、日志、Download/、缓存、EXE/DLL 及旧发行压缩包
- [ ] 在 GitHub 检查文件列表、提交历史及安全扫描结果
- [ ] 确认 PolyForm Noncommercial 1.0.0 为本项目原创代码许可，明确不是 OSI 开源
- [ ] 仓库所有者核准后才由 Private 切换 Public

## 公开 Windows EXE / 安装包之前（单独验收）
- [ ] Windows 11 上验证启动、退出、BT/磁力/HTTP、AI 改名、桌宠、音效、横纵背景裁切
- [ ] 核实 PySide6/Qt/FFmpeg 和 aria2 发行对应许可及源码提供义务
- [ ] 重新构建 EXE，不能重复使用仍含旧同人图片的历史安装包
- [ ] 完整的第三方许可清单、SHA256 校验值和 Release Notes
