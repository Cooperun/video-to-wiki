# 🎥 视频转文字 Wiki 自动导入工具 (`video-to-wiki`)

这是一个专为个人知识库（Obsidian/Notion 等 `llm_wiki` 级 RAG 问答）打造的高性能、工业级视频转文字笔记自动提取与归一化工具。

基于**第一性原理**构建，它深度解耦了媒体管道与 AI 大模型。支持一键全局安装，能在您系统的任何目录下直接通过命令拉起，对 AI 代理（Agents）和开发环境提供极强、极友好的支持。

---

## ✨ 核心特性

- **🚀 全局 CLI 命令行执行**：支持使用 `pip3 install -e .` 注册系统全局命令 `video-to-wiki`。可在终端任意路径瞬间启动，无路径报错阻碍。
- **⚡ Subtitle-First 字幕优先引擎**：针对在线视频（Bilibili、YouTube），优先快速探测和下载在线精细字幕（官方或自动生成）。**成功匹配时，100% 绕过视频/音频下载和本地 ASR 转录**，将处理耗时由数分钟缩短至 **15 秒以内**！
- **🎙️ 本地 ASR 优雅降级**：若在线字幕缺失（如本地视频或闭源平台），系统自动以高可靠性降级到本地 `faster-whisper-base` 引擎，进行高精度的本地离线语音识别。
- **🧹 OralSanitizer 本地口语废词过滤与紧凑合并**：
  - **废词精剪**：自动清洗 `就是说`、`那什么`、`呃`、`啊` 等数十种高频中文叹词与口癖。
  - **10倍语义段落重组**：将原本 ASR 出的数百个 0.5s~1s 零碎微片段，智能重组为带精确标点、长短适宜（150字内）的连贯“语义段落”，**使大模型推理生成提速 16% 以上**，并为以后的 Hermes RAG 块分割提供无可比拟的高内聚物理断句。
- **🧠 辩证型旗舰 DeepSeek-V4-Pro 总结**：原生对接官方 2026 最新 reasoning 思考规范，提供极高事实密度的技术剖析，并提供 ASR 专业口误纠偏与多维辩证争议点剖析。
- **📂 4级配置文件自动检索**：无需在当前执行目录下放置 `config.yaml`。AppConfig 会根据以下四级防线自动定位您的 Obsidian wiki 路径：
  1. 命令行参数 `--config` 手动指定。
  2. 当前工作目录（CWD）下的 `./config.yaml`。
  3. **用户主目录全局统一配置**：`~/.config/video-to-wiki/config.yaml`（最推荐）。
  4. 软件内置硬编码 defaults 路径。

---

## 📺 支持的视频平台与链接范围

本工具专注于对主流技术视频与知识分享平台进行高精度的知识提取，已通过端到端测试验证的在线支持范围如下：

### 1. 支持的在线视频平台
*   🇨🇳 **哔哩哔哩 (Bilibili)**
    *   **支持范围**：标准单视频（如 `https://www.bilibili.com/video/BV1xxxxxx/`）、短链接（如 `https://b23.tv/xxxxxx`）、多 P 视频（默认读取第一 P，或通过 URL 中包含 `?p=N` 精准导入指定分 P）。
    *   **特别说明**：*为了保护隐私，本项目的 README 示例中已全部去除了任何真实的 Bilibili 视频链接，统一采用脱敏的 `BV1xxxxxx` 占位符进行演示。*
*   🌎 **YouTube**
    *   **支持范围**：标准播放页面链接（如 `https://www.youtube.com/watch?v=xxxxxx`）、Shorts 短视频链接（如 `https://www.youtube.com/shorts/xxxxxx`）、分享短链（如 `https://youtu.be/xxxxxx`）。

### 2. 其他平台/加密视频的本地导入机制
> [!TIP]
> 针对非上述原生支持的在线视频平台（如微信视频号、录屏会议、或带有强防爬加密校验的视频链接），直接传入在线 URL 可能会由于平台风控或解析限制而失败。
>
> **通用保底方案**：您只需将视频导出或录屏为本地音视频文件（支持 `.mp4`、`.mkv`、`.mov`、`.mp3` 等常见格式），在运行 CLI 时切换为本地文件模式即可，转写与生成效果同样完美：
> ```bash
> video-to-wiki --file "/path/to/your_video.mp4"
> ```

---

## 🛠️ 全局 CLI 初始化指南

本工具支持在 Mac Apple Silicon（M1/M2/M3/M4）上本地免 C++ 编译快速部署。

### 1. 安装系统音视频依赖
本工具调用 ffmpeg 进行音视频处理，请先使用 Homebrew 一键安装：
```bash
brew install ffmpeg
```

### 2. 全局本地安装 CLI 工具
在克隆或下载的工程根目录下，执行如下命令进行全局“可编辑开发模式”安装：
```bash
pip3 install -e .
```
安装完成后，您可以在系统的任意路径下输入下述命令，确认全局可执行程序已正确挂载：
```bash
which video-to-wiki
```
> [!NOTE]
> 如果安装后提示 `video-to-wiki not found`，这说明您系统的 Python 用户可执行二进制目录未挂载到系统 `PATH` 路径中。
> 建议在您的 `~/.zshrc` 或 `~/.bash_profile` 中添加：`export PATH="$PATH:$HOME/Library/Python/3.9/bin"` 即可解决。

### 3. 配置全局 API Key 与 Wiki 目录
我们极其推荐您把全局配置文件放在 `~/.config/video-to-wiki/config.yaml` 中，这样无论您在哪个文件夹下运行，都能输出至统一的本地 Obsidian 中。
1. 在您的主目录下创建全局文件夹并把配置复制过去：
   ```bash
   mkdir -p ~/.config/video-to-wiki
   cp config.yaml ~/.config/video-to-wiki/config.yaml
   ```
2. 用文本编辑器打开该全局配置，将 `wiki_dir` 修改为您本机 `llm_wiki` 视频库的绝对路径：
   ```yaml
   wiki_dir: "/Users/byron/Documents/antigravity/llm_wiki"
   ```
3. **环境变量**：确保您的 `~/.zshrc` 或 `~/.bash_profile` 中已注入了 DeepSeek 官方 API 密钥：
   ```bash
   export DEEPSEEK_API_KEY="您的真实密钥"
   ```

---

## 🚀 命令行使用指南

挂载为全局 CLI 后，您可以在任何目录下直接执行 `video-to-wiki`：

### A. 在线视频一键极速转文字 Wiki (优先在线提取字幕)
```bash
video-to-wiki --url "https://www.bilibili.com/video/BV1xxxxxx/"
```
*如果视频包含可用字幕，将触发极速引擎，在十几秒内秒出排版精美的 Markdown 研报；若无字幕，自动降级为本地 ASR 解码。*

### B. 本地视频文件一键转文字 Wiki (如微信视频号本地备份)
针对无法直链拉取字幕的本地 `.mp4` / `.mkv` 备份文件，传入文件路径：
```bash
video-to-wiki --file "/Users/byron/Movies/recording.mp4"
```

### C. 可选参数重载
*   **手动覆盖大模型名称**：
    ```bash
    video-to-wiki --url "https://www.bilibili.com/video/BV1xxxxxx/" --model "deepseek-v4-pro"
    ```
*   **调试模式（保留音频/字幕临时缓存）**：
    ```bash
    video-to-wiki --url "https://..." --keep-temp
    ```
*   **手动覆盖配置文件路径**：
    ```bash
    video-to-wiki --url "https://..." --config "/path/to/my-config.yaml"
    ```

---

## 📁 Wiki 输出目录结构规范

IngestionPipeline 处理完成后，您的 `llm_wiki` 文件夹中将生成如下标准化事实资产：

```
llm_wiki/
└── 视频知识库/
    ├── manifests/
    │   └── [视频标题].manifest.json      # 导入记录元数据 (schema_version 1)
    └── [视频标题].md                    # 口语过滤、排版极其考究的 Markdown 研报
```
每个 Markdown 后均自带带精确定位时间戳的 `## 可用于后续问答的事实` 与 `## 原始转写时间线` 围栏，为 Hermes 提供了高内聚的基础语篇。


