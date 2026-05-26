import os
import sys
import json
import logging

# Ensure src is in the import path
sys.path.insert(0, os.path.abspath(os.path.dirname(__file__)))

from src.config import AppConfig
from src.normalizer import TermNormalizer

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

def test_article_level_self_healing():
    print("==================================================")
    print("🧪 [TEST] 开始测试 ASR 纠偏全局校验与自愈闭环逻辑")
    print("==================================================")

    # 1. Initialize configuration to get API keys
    config = AppConfig()
    api_key = config.deepseek_api_key if config.provider == "deepseek" else config.openai_compat_api_key
    api_base = config.deepseek_api_base if config.provider == "deepseek" else config.openai_compat_api_base
    model = config.deepseek_model if config.provider == "deepseek" else config.openai_compat_model
    
    if not api_key:
        print("❌ 错误: 未检测到有效的 API Key，无法运行真实的 LLM 校验测试！")
        sys.exit(1)

    print(f"Using Provider: {config.provider}")
    print(f"Using Model: {model}")
    print(f"Using EndPoint: {api_base}")

    # 2. Define a temporary custom corrections path
    temp_json_path = os.path.abspath("./test_custom_corrections.json")
    if os.path.exists(temp_json_path):
        os.remove(temp_json_path)

    try:
        # 3. Instantiate TermNormalizer
        normalizer = TermNormalizer(custom_corrections_path=temp_json_path)
        
        # 4. Prepare a mockup scenario
        # Original ASR says "clothcode" and "clothcode md"
        # The initial corrections erroneously mapped "clothcode" -> "Cline"
        # and "clothcode md" -> "CLAUDE.md"
        original_transcript = (
            "工具我使用的是 clothcode，大家可以在 VS Code 里面搜索下载它，然后在项目根目录下，"
            "我们通常要新建一个 clothcode md 文件，这个文件是专门写给 AI 看了，里面可以编写我们项目的开发规范和限制指南。"
        )
        
        initial_corrections = [
            {
                "original_typo": "clothcode",
                "corrected_term": "Cline",
                "evidence_source": "DynamicHypothesis",
                "match_count": 1,
                "status": "replaced"
            },
            {
                "original_typo": "clothcode md",
                "corrected_term": "CLAUDE.md",
                "evidence_source": "LocalStaticKB",
                "match_count": 1,
                "status": "replaced"
            }
        ]

        # The generated article context containing the conflict
        markdown_content = (
            "# Vibe Coding 纯小白零代码开发指南\n\n"
            "## 核心工具\n"
            "本次教程推荐使用 AI 编程助手 **Cline** (即 clothcode)。Cline 是一个基于 VS Code 的插件，支持自动编写代码。\n\n"
            "## 关键配置文件配置\n"
            "为了限制 AI 的自动修改权限，我们需要在项目根目录下创建一个名为 **CLAUDE.md** 的配置文件。\n"
            "CLAUDE.md 文件包含了项目的编译、测试、规范指令，使得 Cline 在工作时能够完美遵循规则。\n\n"
            "## 总结\n"
            "配置好 CLAUDE.md 之后，你的 Cline 将变成极度高效的开发助手。"
        )

        print("\n--- 原始错误纠偏上下文 (ASR Normalization Conflict) ---")
        print("发现矛盾：CLAUDE.md 属于 Claude Code 专属配置文件，而 clothcode (音近 Claude Code) 却被错误地纠偏为了 Cline。")
        
        # 5. Execute verify_and_heal_article
        healed_mappings = normalizer.verify_and_heal_article(
            original_transcript=original_transcript,
            canonical_text="Cline and CLAUDE.md",  # Shortened canonical transcript context
            markdown_content=markdown_content,
            initial_corrections=initial_corrections,
            api_key=api_key,
            api_base=api_base,
            model=model
        )

        print(f"\n🔮 [Audit Output] 审计系统返回的自愈重写字典: {healed_mappings}")

        # 6. Verify that "clothcode" was corrected to "Claude Code"
        assert healed_mappings, "测试失败: 审计未发现任何技术冲突！"
        
        found_target = False
        for k, v in healed_mappings.items():
            if k.lower() == "clothcode" and v.strip() == "Claude Code":
                found_target = True
                
        assert found_target, f"测试失败: 纠偏映射不正确，应该包含 clothcode -> Claude Code, 实际为: {healed_mappings}"
        print("✅ [Pass 1] 成功在文章语境中检测到 Cline 与 CLAUDE.md 的技术冲突，并推导出了正确的 Claude Code 映射！")

        # 7. Persist corrections to the JSON file
        normalizer.save_custom_corrections(healed_mappings)

        # 8. Check if custom_corrections.json is written correctly
        assert os.path.exists(temp_json_path), "测试失败: 未成功生成持久化 custom_corrections.json 文件！"
        
        with open(temp_json_path, "r", encoding="utf-8") as f:
            saved_data = json.load(f)
            print(f"\n💾 [Saved JSON File Content]:\n{json.dumps(saved_data, ensure_ascii=False, indent=2)}")
            
        assert saved_data.get("clothcode") == "Claude Code", "测试失败: 持久化文件中的映射不正确！"
        print("✅ [Pass 2] 成功将纠正后的映射增量持久化存储至本地 JSON 文件中！")

        # 9. Verify that a second TermNormalizer instance loads it on init
        new_normalizer = TermNormalizer(custom_corrections_path=temp_json_path)
        assert new_normalizer.static_kb.get("clothcode") == "Claude Code", "测试失败: 新实例未成功加载持久化的纠偏词！"
        print("✅ [Pass 3] 验证第二实例成功加载历史持久化词库！前置纠偏“自学习”已闭环生效！")
        
        print("\n🎉 [Success] 所有自愈及持久化测试项全部顺利通过！")
        
    finally:
        # Cleanup
        if os.path.exists(temp_json_path):
            os.remove(temp_json_path)
            print("🧹 临时持久化 JSON 文件已成功清空。")

if __name__ == "__main__":
    test_article_level_self_healing()
