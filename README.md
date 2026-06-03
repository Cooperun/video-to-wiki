# 🎥 视频转文字 Wiki 自动导入工具 (`video-to-wiki`)

这是一个专为个人知识库（Obsidian/Notion 等 `llm_wiki` 级 RAG 问答）打造的高性能、工业级视频转文字笔记自动提取与归一化工具。

基于**第一性原理**构建，它深度解耦了媒体管道与 AI 大模型。支持一键全局安装，能在您系统的任何目录下直接通过命令拉起，对 AI 代理（Agents）和开发环境提供极强、极友好的支持。

---

## ✨ 核心特性

- **🚀 全局 CLI 命令行执行**：支持使用 `pip3 install -e .` 注册系统全局命令 `video-to-wiki`。可在终端任意路径瞬间启动，无路径报错阻碍。
- **⚡ Subtitle-First 字幕优先引擎**：针对在线视频（Bilibili、YouTube），优先快速探测和下载在线精细字幕（官方或自动生成）。**成功匹配时，100% 绕过视频/音频下载和本地 ASR 转录**，将处理耗时由数分钟缩短至 **15 秒以内**！
- **🎙️ 本地 ASR 优雅降级**：若在线字幕缺失（如本地视频或闭源平台），系统自动以高可靠性降级到本地 `faster-whisper-base` 引擎，进行高精度的本地离线语音识别。
- **👁️ Hybrid 硬字幕 OCR 引擎**：针对没有在线字幕、但视频画面自带硬字幕的内容，默认启用 `hybrid` 模式：本地 RapidOCR 先识别，低置信度帧自动升级到 Qwen-VL 云端精修。相比全云端 OCR，大幅减少 API 调用；相比纯本地 OCR，又保留疑难帧兜底能力。
- **📐 自动字幕区域定位**：抽帧后自动用本地 CV 检测画面底部字幕区域，只裁剪字幕 ROI 送入 OCR，减少屏幕 UI、代码窗口、弹幕等无关内容对识别的干扰。
- **🧹 OralSanitizer 本地口语废词过滤与紧凑合并**：
  - **废词精剪**：自动清洗 `就是说`、`那什么`、`呃`、`啊` 等数十种高频中文叹词与口癖。
  - **10倍语义段落重组**：将原本 ASR 出的数百个 0.5s~1s 零碎微片段，智能重组为带精确标点、长短适宜（150字内）的连贯“语义段落”，**使大模型推理生成提速 16% 以上**，并为以后的 Hermes RAG 块分割提供无可比拟的高内聚物理断句。
- **🩺 ASR/OCR 术语纠偏与自愈**：内置专有名词纠偏词库，支持持久化 `custom_corrections.json`。最终文章生成后，会用文章语义反向清洗转写时间线，避免把 `DPC`、`Pain Mode`、`clothcode md`、孤立乱码等 ASR/OCR 噪声直接留在知识库文档里。
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

## ✅ 运行依赖与硬件要求

本项目不是单纯的文本处理脚本，它会同时调用下载器、音视频工具、本地 ASR、本地/云端 OCR 和大模型总结服务。建议先确认下面这些依赖，再开始批量导入视频。

### 1. 基础运行环境

| 依赖 | 要求 | 用途 |
| --- | --- | --- |
| 操作系统 | macOS / Linux | 当前主要按 Mac Apple Silicon 与主流 Linux 环境验证 |
| Python | 3.9+，推荐 3.9 - 3.11 | CLI、ASR、OCR、模型调用主运行时 |
| pip | 与当前 Python 版本匹配 | 安装 `requirements.txt` 中的 Python 包 |
| 网络 | 在线视频、首次模型下载、云端模型调用时需要 | Bilibili/YouTube 下载、DashScope/DeepSeek/OpenAI Compatible API |

> [!NOTE]
> Python 3.12+ 是否顺利安装，取决于 `faster-whisper`、`onnxruntime`、`easyocr` 等底层包在当前平台是否已有可用 wheel。生产使用更推荐 Python 3.9 - 3.11。

### 2. 系统级依赖

| 工具 | 是否必需 | 安装示例 | 用途 |
| --- | --- | --- | --- |
| `ffmpeg` | 必需 | macOS: `brew install ffmpeg` | 抽音频、抽帧、截图、媒体预处理 |
| `yt-dlp` | 在线视频必需 | `pip3 install yt-dlp` 或 `brew install yt-dlp` | 下载 Bilibili/YouTube 视频、探测在线字幕 |

`video-to-wiki --init` 会自动检测 `ffmpeg` 和 `yt-dlp` 是否可用，但不会替您安装系统工具。

### 3. Python 包依赖

执行 `pip3 install -e .` 时会读取 `requirements.txt` 并安装核心 Python 依赖：

| 包 | 用途 |
| --- | --- |
| `openai` | 统一调用 DeepSeek、Qwen 百炼兼容端点、OpenAI Compatible 网关 |
| `pyyaml` | 读取 `config.yaml` 配置 |
| `yt-dlp` | Python 环境中的视频下载工具链 |
| `faster-whisper` | 本地 ASR 语音识别 |
| `Pillow` | 图片读取、裁剪、字幕 ROI 处理 |
| `numpy` | 图像数组与字幕区域检测 |
| `rapidocr-onnxruntime` | 默认本地 OCR 引擎，适合中文字幕硬字幕 |
| `easyocr` | 本地 OCR 兜底引擎，RapidOCR 不可用时使用 |

建议额外安装：

```bash
pip3 install opencv-python
```

`opencv-python` 用于本地字幕区域定位与 OCR 前图像预处理。缺失时程序会降级到保底字幕区域，但硬字幕识别质量和抗干扰能力会下降。

### 4. API Key 与云端依赖

| 配置项 / 环境变量 | 何时需要 | 说明 |
| --- | --- | --- |
| `DEEPSEEK_API_KEY` | 默认总结链路需要 | `provider: deepseek` 时用于生成最终 Wiki 笔记 |
| `OPENAI_API_KEY` | 使用 OpenAI Compatible 时需要 | 适用于 OneAPI、NewAPI、LiteLLM、Ollama 兼容网关等 |
| `DASHSCOPE_API_KEY` | 使用 Qwen-VL 或 hybrid 云端 OCR 兜底时需要 | `--ocr-mode cloud` 必需；`--ocr-mode hybrid` 在低置信度帧兜底时需要 |

如果只做本地 ASR 与纯本地 OCR，理论上可以减少云端视觉调用；但生成最终结构化 Wiki 正文仍需要一个文本大模型 provider。

文本总结模型和云端 OCR 模型是两套配置：

- 文本总结：由 `provider` 决定，读取 `deepseek.*`、`openai_compatible.*` 或 `qwen.model`。
- 云端 OCR / hybrid 兜底：读取 `qwen.api_base`、`qwen.api_key`、`qwen.ocr_model`。

OCR 运行规则：

- `--ocr-mode local`：只使用本地 RapidOCR/EasyOCR，不需要云端 OCR Key。
- `--ocr-mode hybrid`：本地 OCR 优先；有 `qwen.api_key` 时低置信度帧会云端兜底，没有 Key 时自动退化为纯本地 OCR。
- `--ocr-mode cloud`：只使用云端视觉 OCR，必须配置 `DASHSCOPE_API_KEY` / `BAILIAN_API_KEY` 或 `qwen.api_key`。

### 5. 模型与缓存

首次运行时，以下组件可能自动下载模型文件：

| 组件 | 默认位置 | 说明 |
| --- | --- | --- |
| `faster-whisper` | HuggingFace / CTranslate2 默认缓存目录 | 首次 ASR 会下载 `base` 或 `small` 模型 |
| `RapidOCR` | Python 包/ONNXRuntime 默认缓存 | 通常随包或首次初始化加载 |
| `EasyOCR` | `~/.EasyOCR/` | 仅在启用或回退 EasyOCR 时下载检测/识别模型 |

建议预留至少 **5GB** 可用磁盘空间。若使用 `--keep-temp` 保留临时文件，长视频会额外占用视频、音频、抽帧和字幕文件空间。

### 6. 硬件建议

| 场景 | 最低配置 | 推荐配置 |
| --- | --- | --- |
| 有在线字幕的视频 | 2 核 CPU / 4GB RAM | 4 核 CPU / 8GB RAM |
| 无在线字幕，走本地 ASR | 4 核 CPU / 8GB RAM | Apple Silicon M1+ 或 6 核以上 CPU / 16GB RAM |
| 硬字幕 OCR `hybrid` | 4 核 CPU / 8GB RAM | 8 核 CPU / 16GB RAM，网络稳定 |
| 批量长视频导入 | 8GB RAM / 10GB 空闲磁盘 | 16GB+ RAM / 20GB+ 空闲磁盘 |

当前 ASR 默认使用 `faster-whisper` 的 CPU `int8` 模式，Apple Silicon 上表现已经足够实用。EasyOCR 会尝试检测 CUDA 或 Apple MPS，但 GPU 不是必需条件。

### 7. 平台注意事项

- **macOS Apple Silicon**：推荐使用 Homebrew 安装 `ffmpeg`，Python 使用 3.9 - 3.11；首次安装 OCR/ASR 依赖可能较慢。
- **Linux**：建议使用系统包管理器安装 `ffmpeg`，例如 Ubuntu/Debian: `sudo apt install ffmpeg`；服务器环境建议准备 16GB 内存用于批量任务。
- **YouTube**：`yt-dlp` 需要保持较新版本；遇到 YouTube `n challenge` 或下载失败时，优先升级 `yt-dlp`。
- **Bilibili**：未登录/无 cookie 时，下载清晰度可能受平台限制；这不影响流程正确性，但会影响硬字幕 OCR 的清晰度上限。

---

## 🛠️ 全局 CLI 初始化与一键配置 (Quickstart)

本工具支持在 Mac Apple Silicon（M1/M2/M3/M4）以及主流 Linux 环境上快速本地部署。

### 1. 全局本地安装 CLI 工具
在克隆或下载的工程根目录下，执行如下命令进行全局可编辑开发模式挂载：
```bash
pip3 install -e .
```

### 2. 运行一键智能初始化命令
安装完成后，您**不需要手动创建文件夹、复制配置文件或手动修改 PATH 环境变量**。我们为此内置了全自动的一键初始化与依赖检测命令。

只需在终端中执行：
```bash
# 执行一键智能初始化
video-to-wiki --init
```
*(提示：若安装后暂时提示 command not found，请直接使用 Python bin 全路径拉起初始化：`/Users/byron/Library/Python/3.9/bin/video-to-wiki --init`)*

**`--init` 命令会自动为您执行以下工作：**
- 📂 **配置文件夹挂载**：自动在主目录下创建全局文件夹并复制默认 `config.yaml` 模板（位于 `~/.config/video-to-wiki/config.yaml`）。
- 🔍 **音视频依赖校验**：自动在本地系统检索并验证 `ffmpeg` 与 `yt-dlp` 底层工具链的可用性。
- 🧭 **交互式模型接入向导**：引导配置 Wiki 输出目录、主文本大模型 provider、OpenAI 兼容网关/中间件，以及 Qwen-VL OCR 云端兜底。
- 🔄 **环境变量自动补全**：自动探测您当前使用的 Shell 类型（Zsh 或 Bash），检测您的全局 `PATH` 路径，若未包含 CLI 命令所在目录，将**全自动追加环境变量到您的 `~/.zshrc` 或 `~/.bash_profile`** 中，彻底告别手动配置！

### 3. 配置您的密钥
初始化向导会优先帮助您选择一个可快速接入的文本大模型通道：

| 通道 | 适合场景 | 必填配置 |
| --- | --- | --- |
| OpenAI 兼容协议/中间件 | 最通用，适合 OpenAI、OneAPI、NewAPI、LiteLLM、OpenRouter、SiliconFlow、Ollama 等 | `api_base`、`api_key`、`model` |
| DeepSeek 官方 API | 直接使用 DeepSeek 官方服务 | `DEEPSEEK_API_KEY` 或 `deepseek.api_key` |
| Qwen 百炼原生通道 | 同一组 DashScope Key 兼顾文本与视觉链路 | `DASHSCOPE_API_KEY` 或 `qwen.api_key` |

您可以把 Key 写入全局配置文件：`nano ~/.config/video-to-wiki/config.yaml`，也可以用环境变量方式注入，例如：

```bash
export OPENAI_API_KEY="您的 OpenAI 兼容网关密钥"
export DEEPSEEK_API_KEY="您的 DeepSeek 密钥"
export DASHSCOPE_API_KEY="您的百炼密钥"
export SILICONFLOW_API_KEY="您的 SiliconFlow 密钥"
export OPENROUTER_API_KEY="您的 OpenRouter 密钥"
```

> [!TIP]
> 默认硬字幕 OCR 是 `hybrid`：本地 RapidOCR 优先，低置信度帧用 Qwen-VL 云端兜底。若暂时没有 DashScope Key，可在初始化向导中改为 `local`，先以纯本地 OCR 跑通流程。

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
*   **指定硬字幕 OCR 模式**：
    ```bash
    # 默认推荐：本地 RapidOCR 优先，低置信度帧云端兜底
    video-to-wiki --url "https://..." --ocr-mode hybrid

    # 全云端 Qwen-VL OCR，适合做高精度对照测试
    video-to-wiki --url "https://..." --ocr-mode cloud

    # 纯本地 OCR，适合离线或零 API 成本场景
    video-to-wiki --file "/path/to/video.mp4" --ocr-mode local
    ```
*   **跳过嵌入式硬字幕 OCR（只跑在线字幕/ASR）**：
    ```bash
    video-to-wiki --url "https://..." --no-ocr
    ```
*   **仅提取硬字幕 SRT/Markdown（不生成 Wiki 正文）**：
    ```bash
    video-to-wiki --url "https://..." --extract-subtitle --ocr-mode hybrid
    ```
*   **手动覆盖配置文件路径**：
    ```bash
    video-to-wiki --url "https://..." --config "/path/to/my-config.yaml"
    ```

---

## 📁 Wiki 输出目录结构规范

IngestionPipeline 处理完成后，您的 `llm_wiki` 文件夹中默认只保留最终 Wiki 文章。硬字幕 OCR 的 SRT/Markdown 中间产物只参与内部校验和时间线修复，不再写入最终结果目录。

```
llm_wiki/
└── 视频知识库/
    └── [视频标题].md                    # 口语过滤、术语修复后的最终 Markdown 研报
```
每个 Markdown 后均自带带精确定位时间戳的 `## 可用于后续问答的事实` 与 `## 校正后转写时间线` 围栏。时间线会结合最终正文进行术语修复和噪声清洗，不再直接发布未处理的 ASR/OCR 原始流水。

如需调试或单独导出硬字幕文件，可显式运行 `--extract-subtitle`，此模式会按需生成 `_纯视觉字幕.srt` 与 `_纯视觉字幕.md`。

---

## 📊 质量评估指标

当视频没有在线字幕并触发 ASR + OCR 流程时，命令行会输出以下关键指标，便于判断本次入库质量：

- **ASR-OCR 语音覆盖率**：ASR 语义段中有多少能在视觉字幕时间线上找到重叠字幕。
- **OCR 模式与调用次数**：显示 `cloud/local/hybrid`，以及云端与本地 OCR 调用量。
- **Hybrid 升级精修次数**：本地 OCR 低置信度、交给云端 Qwen-VL 兜底的帧数。
- **OCR 重复率**：按实际 OCR 尝试次数统计重复识别，反映视频字幕停留时间、差分阈值和采样密度的综合效果。

实际测试中，`hybrid` 模式在 Bilibili 技术视频上可将云端 OCR 调用控制在低个位数百分比，同时保留接近全云端 OCR 的字幕覆盖质量。
