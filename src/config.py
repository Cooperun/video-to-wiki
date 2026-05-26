import os
import yaml
import logging

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

class AppConfig:
    def __init__(self, config_path=None):
        self.config_path = config_path
        
        # Core built-in defaults (Level 4 Fallback)
        self.provider = "deepseek"
        self.wiki_dir = os.path.expanduser("~/Documents/antigravity/llm_wiki")
        self.temp_dir = "./temp"
        self.asr_model_size = "base"
        self.asr_language = "zh"
        self.asr_subtitle_first = True
        
        self.scene_threshold = 0.02
        self.max_interval_sec = 15
        self.max_width = 1280
        self.visual_min_frames = 0
        self.visual_max_frames = 8
        self.visual_trigger_window_sec = 6
        self.visual_min_gap_sec = 12
        self.visual_min_score = 2.5
        self.enable_images = False
        self.image_link_style = "standard"
        
        # Qwen settings
        self.qwen_api_key = ""
        self.qwen_model = "qwen3-vl-plus"
        self.qwen_visual_locator_model = "qwen3-vl-plus"
        self.qwen_composer_model = "qwen3-vl-plus"
        self.qwen_api_base = "https://dashscope.aliyuncs.com/compatible-mode/v1"
        
        # DeepSeek settings
        self.deepseek_api_key = ""
        self.deepseek_model = "deepseek-v4-pro"
        self.deepseek_api_base = "https://api.deepseek.com"
        self.deepseek_enable_thinking = True
        self.deepseek_reasoning_effort = "high"
        self.deepseek_structuring_prompt = ""

        # OpenAI Compatible settings
        self.openai_compat_api_key = ""
        self.openai_compat_api_base = ""
        self.openai_compat_model = ""
        self.openai_compat_structuring_prompt = ""

        # Search Grounding settings
        self.search_grounding_enabled = False
        self.search_max_keywords = 3
        self.search_max_search_results = 3

        # Subtitle OCR settings
        self.ocr_mode = "hybrid"                    # 'cloud' / 'local' / 'hybrid'
        self.ocr_local_engine = "rapidocr"          # 'rapidocr' / 'easyocr' / 'auto'
        self.ocr_local_confidence_threshold = 0.5   # hybrid: below this → escalate to cloud

        # Custom Corrections file path (for persistent ASR normalizer dictionary)
        self.custom_corrections_path = os.path.abspath("./custom_corrections.json")
        
        self.load_config()

    def load_config(self):
        # 1. Sourcing shell profiles first to inherit credential keys seamlessly
        root_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        self._load_env_file(os.path.join(root_dir, ".env"))
        self._load_env_file(os.path.expanduser("~/.zshrc"))
        self._load_env_file(os.path.expanduser("~/.zprofile"))
        self._load_env_file(os.path.expanduser("~/.zshenv"))

        # 2. 4-Level Configuration File Finder
        resolved_path = None
        
        # Level 1: Explicit custom override
        if self.config_path:
            explicit_path = os.path.abspath(os.path.expanduser(self.config_path))
            if not os.path.exists(explicit_path):
                raise FileNotFoundError(f"显式指定的配置文件不存在: {explicit_path}")
            resolved_path = explicit_path
        else:
            # Level 2: CWD `./config.yaml`
            cwd_path = os.path.abspath("./config.yaml")
            if os.path.exists(cwd_path):
                resolved_path = cwd_path
            else:
                # Level 3: Global user config `~/.config/video-to-wiki/config.yaml`
                home_path = os.path.expanduser("~/.config/video-to-wiki/config.yaml")
                if os.path.exists(home_path):
                    resolved_path = home_path

        # Level 4: Built-in Defaults Fallback
        if not resolved_path:
            logging.info("未匹配到任何 config.yaml 配置文件。将启用内置默认参数进行初始化...")
            self._validate()
            self._resolve_keys()
            
            # Make relative temp_dir absolute relative to package root
            if not os.path.isabs(self.temp_dir):
                self.temp_dir = os.path.abspath(os.path.join(root_dir, self.temp_dir))
            os.makedirs(self.temp_dir, exist_ok=True)
            return

        self.config_path = resolved_path
        logging.info(f"成功加载配置文件: {self.config_path}")
        self.custom_corrections_path = os.path.join(os.path.dirname(self.config_path), "custom_corrections.json")
        
        with open(self.config_path, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
            
        self.provider = data.get("provider", "deepseek")
        self.wiki_dir = os.path.abspath(os.path.expanduser(data.get("wiki_dir", "~/Documents/antigravity/llm_wiki")))
        self.temp_dir = data.get("temp_dir", "./temp")
        if not os.path.isabs(self.temp_dir):
            self.temp_dir = os.path.abspath(os.path.join(root_dir, self.temp_dir))
        
        # ASR Config
        asr = data.get("asr", {})
        self.asr_model_size = asr.get("model_size", "base")
        self.asr_language = asr.get("language", "zh")
        self.asr_subtitle_first = asr.get("subtitle_first", True)
        
        # Sampling Config
        sampling = data.get("sampling", {})
        self.scene_threshold = sampling.get("scene_threshold", 0.02)
        self.max_interval_sec = sampling.get("max_interval_sec", 15)
        self.max_width = sampling.get("max_width", 1280)

        # Visual Locator Config
        visual_locator = data.get("visual_locator", {})
        self.enable_images = visual_locator.get("enabled", False)
        self.visual_min_frames = visual_locator.get("min_frames", 0)
        self.visual_max_frames = visual_locator.get("max_frames", 8)
        self.visual_trigger_window_sec = visual_locator.get("trigger_window_sec", 6)
        self.visual_min_gap_sec = visual_locator.get("min_gap_sec", 12)
        self.visual_min_score = visual_locator.get("min_score", 2.5)

        # Output Config
        output = data.get("output", {})
        self.image_link_style = output.get("image_link_style", "standard")
        
        # Qwen Config
        qwen = data.get("qwen", {})
        self.qwen_api_key = qwen.get("api_key", "")
        self.qwen_model = qwen.get("model", "qwen3-vl-plus")
        self.qwen_visual_locator_model = qwen.get("visual_locator_model", self.qwen_model)
        self.qwen_composer_model = qwen.get("composer_model", self.qwen_model)
        self.qwen_api_base = qwen.get("api_base", "https://dashscope.aliyuncs.com/compatible-mode/v1")

        # DeepSeek Config
        deepseek = data.get("deepseek", {})
        self.deepseek_api_key = deepseek.get("api_key", "")
        self.deepseek_model = deepseek.get("model", "deepseek-v4-pro")
        self.deepseek_api_base = deepseek.get("api_base", "https://api.deepseek.com")
        self.deepseek_enable_thinking = deepseek.get("enable_thinking", True)
        self.deepseek_reasoning_effort = deepseek.get("reasoning_effort", "high")
        self.deepseek_structuring_prompt = deepseek.get("structuring_prompt", "")

        # OpenAI Compatible Config
        openai_compat = data.get("openai_compatible", {})
        self.openai_compat_api_key = openai_compat.get("api_key", "")
        self.openai_compat_api_base = openai_compat.get("api_base", "")
        self.openai_compat_model = openai_compat.get("model", "")
        self.openai_compat_structuring_prompt = openai_compat.get("structuring_prompt", "")

        # Search Grounding Config
        search = data.get("search_grounding", {})
        self.search_grounding_enabled = search.get("enabled", False)
        self.search_max_keywords = search.get("max_keywords", 3)
        self.search_max_search_results = search.get("max_search_results", 3)

        # Subtitle OCR Config
        subtitle_ocr = data.get("subtitle_ocr", {})
        self.ocr_mode = subtitle_ocr.get("mode", "hybrid").lower().strip()
        self.ocr_local_engine = subtitle_ocr.get("local_engine", "rapidocr").lower().strip()
        self.ocr_local_confidence_threshold = float(
            subtitle_ocr.get("local_confidence_threshold", 0.5)
        )
        self._validate()

        self._resolve_keys()
        os.makedirs(self.temp_dir, exist_ok=True)

    def _validate(self):
        provider_choices = {"qwen", "deepseek", "openai_compatible"}
        if self.provider not in provider_choices:
            raise ValueError(f"provider 配置非法: {self.provider}，可选: {', '.join(sorted(provider_choices))}")

        ocr_choices = {"cloud", "local", "hybrid"}
        if self.ocr_mode not in ocr_choices:
            raise ValueError(f"subtitle_ocr.mode 配置非法: {self.ocr_mode}，可选: {', '.join(sorted(ocr_choices))}")

        local_engine_choices = {"rapidocr", "easyocr", "auto"}
        if self.ocr_local_engine not in local_engine_choices:
            raise ValueError(
                f"subtitle_ocr.local_engine 配置非法: {self.ocr_local_engine}，"
                f"可选: {', '.join(sorted(local_engine_choices))}"
            )

        if not 0 <= self.ocr_local_confidence_threshold <= 1:
            raise ValueError("subtitle_ocr.local_confidence_threshold 必须在 0 到 1 之间。")

    def _resolve_keys(self):
        # Resolve Qwen API Key from environment if blank in config
        if not self.qwen_api_key:
            self.qwen_api_key = os.environ.get("DASHSCOPE_API_KEY", "")
        if not self.qwen_api_key:
            self.qwen_api_key = os.environ.get("BAILIAN_API_KEY", "")
        if not self.deepseek_api_key:
            self.deepseek_api_key = os.environ.get("DEEPSEEK_API_KEY", "")
        if not self.openai_compat_api_key:
            self.openai_compat_api_key = os.environ.get("OPENAI_API_KEY", "")

    def _load_env_file(self, env_path):
        if not os.path.exists(env_path):
            return

        with open(env_path, "r", encoding="utf-8", errors="ignore") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                if line.startswith("export "):
                    line = line[len("export "):].strip()
                key, value = line.split("=", 1)
                key = key.strip()
                value = value.strip().strip("'").strip('"')
                if key and value and key not in os.environ:
                    os.environ[key] = value
