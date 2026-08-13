# 桌面发布验收清单

本文记录桌面应用的可复现构建、安装包和跨平台验收边界。单元测试通过不等同于安装包可交付；每个平台必须由对应原生 runner 或实机产生证据。

## 构建入口

1. 安装 Python 3.9+、Node.js 22、pnpm 11.16 和 stable Rust。
2. 执行 `python -m pip install -e ".[test,packaging]"`。
3. 执行 `pnpm install --frozen-lockfile`。
4. 执行 `python scripts/build_sidecar.py`，生成当前主机 target triple 对应的 Tauri external binary。
5. 执行 `pnpm tauri build`，或按平台限定 bundle：
   - macOS：`pnpm tauri build --bundles app,dmg`
   - Windows：`pnpm tauri build --bundles nsis,msi`

Tauri 的正式前端钩子直接运行 `node scripts/build_frontend.mjs`，只使用已经安装并由锁文件约束的本地 TypeScript/Vite，不在打包中途再次联网切换包管理器。

## 自动化证据

`.github/workflows/desktop-build.yml` 在 Linux 运行完整 Python、TypeScript 和 Rust 测试，并在原生 macOS、Windows runner 上分别：

- 冻结本平台 Sidecar；
- 验证桌面父进程消失后 Sidecar 自动退出；
- 构建 `.app/.dmg` 或 NSIS/MSI；
- 将安装产物作为 GitHub Actions artifact 保留 14 天。

## macOS 本机基线

- 架构：Apple Silicon `aarch64-apple-darwin`。
- 安装包：`软著材料助手_0.1.0_aarch64.dmg`。
- DMG 大小：约 38 MiB。
- 最终 DMG SHA-256：`8cd1e1589fb0f7be746c44e4f5644677adc739808ad555fc448c75f2b597138b`。
- `hdiutil verify`：通过。
- DMG 包含 Applications 快捷方式、正式 `.app`、ICNS 图标、arm64 桌面壳和 arm64 Sidecar。
- Sidecar SHA-256：`7c6b8ad5831dd4e67f28aace1d8c9291b3585712e392b4c70d84da8a3bf70877`。
- 从只读 DMG 启动成功；Sidecar 使用 `app_data_dir` 对应的应用数据目录，而不是 DMG 临时目录。
- 该历史 DMG 启动前后 SQLite 均为 schema v22、3 个任务、6 个模型配置、1 份加密凭据；当前代码为 schema v31，必须重新构建候选安装包并验证 v22→v31 原地迁移。
- 强制结束 DMG 中的桌面壳后，内嵌 PyInstaller Sidecar 无残留。

## Windows 必验项目

以下项目只有 Windows runner 构建成功或 Windows 10/11 实机执行后才能标记通过：

- `x86_64-pc-windows-msvc` Sidecar 与桌面壳架构一致；
- NSIS 和 MSI 均包含 `.exe` Sidecar、ICO 图标和前端资源；
- 安装、卸载、覆盖升级不会删除 `%APPDATA%` 中的 SQLite 和任务资产；
- 中文、空格、长路径下可选择项目、导入截图、导出 DOCX；
- Explorer `/select,` 能定位导出文件；
- Word/WPS 可打开源代码与说明书 DOCX，中文字体、图表和截图无替换或丢失；
- 桌面壳正常退出、崩溃或被任务管理器终止后没有 Sidecar 残留。

## 发布门禁

### 当前定稿候选基线（2026-08-13）

- 分支：`codex/finalize-automation-workflow`。
- 功能基线提交：`142655f`；后续文档提交只更新项目说明与发布事实。
- 本地回归：Python 183 项通过、前端 production build 通过、Rust 12 项通过、`git diff --check` 通过。
- 用户已确认当前功能版本可定稿；这不等同于两平台签名安装包已经通过公开发布门禁。
- GitHub Actions 必须对该分支执行 `.github/workflows/desktop-build.yml`；若任何原生构建失败，不得把本地验收结果写成跨平台发布成功。

- 当前本地 macOS 包为 ad-hoc 签名，尚未配置 Apple Developer ID、公证和 stapling；只能用于本机开发验收，不能宣称已通过公开分发 Gatekeeper。
- Windows 代码签名证书尚未配置；CI 构建用于兼容性验证，不代表 SmartScreen 发布信誉已建立。
- 正式公开发布前必须配置两平台签名密钥的 GitHub Environment/Secret、生成 SBOM/校验清单，并在干净虚拟机完成安装、升级、卸载和数据保留测试。
- 模型调用仍由用户显式触发；安装包验收不得为了截图或测试自动产生付费调用。
