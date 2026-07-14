# 合规本地职位采集节点

本节点将 BOSS直聘和猎聘从“临时手工导出”升级为本人电脑上的稳定、持久化授权节点，并与云端 08:00 求职管道衔接。

## 安全边界

只读取岗位搜索结果和岗位详情。不会自动投递、打招呼、发送消息、交换联系方式、操作招聘者功能，也不会破解验证码、规避安全验证或轮换代理。

检测到登录失效、验证码、安全验证、访问异常或环境异常时，执行器立即停止，并记录为 `blocked` 或 `condition_unmet`，不会伪装成 `zero_result`。

## Windows首次设置

将压缩包解压到长期保留的目录。以 Google Drive Desktop 已同步的原始快照目录作为输出目录：

```powershell
cd local_node
powershell -ExecutionPolicy Bypass -File .\setup_windows.ps1 `
  -DropDir "G:\My Drive\10_岗位池与公司研究\01_每日职位原始快照"
```

设置过程会安装 `boss-cli` 与 Playwright Chromium，由本人在本机完成 BOSS 登录，并建立专用猎聘浏览器配置目录。凭证不会进入导出包或云端。

## 安装每日节点

```powershell
powershell -ExecutionPolicy Bypass -File .\install_windows_task.ps1 `
  -DropDir "G:\My Drive\10_岗位池与公司研究\01_每日职位原始快照" `
  -At "07:15"
```

07:15 本地采集，Google Drive Desktop 随后同步；08:00 云端日报读取最新 `LOCAL-*` ZIP 与 `manifest.json`。

## 手动运行与健康检查

```powershell
.\run_windows.ps1
.\run_windows.ps1 -Sources "boss"
.\run_windows.ps1 -Sources "liepin"
python ..\validation\source_registry_check.py
python ..\validation\source_control_plane.py health --context local
```

## 输出契约

每次运行产生 `manifest.json`、BOSS与猎聘的 `jobs.json`、`jobs.csv`、`health.json`、查询审计、截图和完整 ZIP。

标准状态：`success`、`zero_result`、`blocked`、`condition_unmet`、`failed`、`not_run`。`zero_result` 只允许在查询真实完成且有审计证据时出现。
