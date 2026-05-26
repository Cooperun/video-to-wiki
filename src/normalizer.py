import re
import json
import logging
import os
from openai import OpenAI

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

class TermNormalizer:
    """
    TermNormalizer decouples ASR term correction from the downstream LLM writing phase.
    It utilizes a local static Knowledge Base (KB), dynamic mappings, persistent user corrections,
    and implements a self-correcting verification loop to ensure logical consistency in context.
    """
    def __init__(self, custom_corrections_path=None):
        # Static local KB for common Vibe Coding and general tech/model ASR typos
        self.static_kb = {
            # Typos -> Correct Term
            "dpc v4 pro": "DeepSeek V4 Pro",
            "dpc v4pro": "DeepSeek V4 Pro",
            "dbcv4 pro": "DeepSeek V4 Pro",
            "dipsig": "DeepSeek",
            "dbc": "DeepSeek",
            "clalicle": "Cline",
            "clothcode md": "CLAUDE.md",
            "clothcode": "Claude Code",
            "cloud code": "Claude Code",
            "viscode": "VS Code",
            "vibocoding": "Vibe Coding",
            "web code": "VS Code"
        }
        self.custom_corrections_path = custom_corrections_path
        self.corrections_log = []
        self.load_custom_corrections()

    def load_custom_corrections(self):
        """
        Loads user custom corrections from a persistent JSON file.
        This allows the system to continuously optimize and remember corrected terms.
        """
        if self.custom_corrections_path and os.path.exists(self.custom_corrections_path):
            try:
                with open(self.custom_corrections_path, "r", encoding="utf-8") as f:
                    custom_kb = json.load(f)
                    if isinstance(custom_kb, dict):
                        for k, v in custom_kb.items():
                            self.static_kb[k.lower().strip()] = v.strip()
                        logging.info(f"💾 [TermNormalizer] 成功加载 {len(custom_kb)} 条历史持久化纠偏词库: {self.custom_corrections_path}")
            except Exception as e:
                logging.warning(f"加载持久化纠偏词库失败: {e}")

    def save_custom_corrections(self, new_mappings):
        """
        Incrementally merges and persists new verified corrections back to custom_corrections.json.
        """
        if not self.custom_corrections_path:
            return

        custom_kb = {}
        if os.path.exists(self.custom_corrections_path):
            try:
                with open(self.custom_corrections_path, "r", encoding="utf-8") as f:
                    custom_kb = json.load(f)
            except Exception as e:
                logging.warning(f"读取持久化词库以进行合并时出错: {e}")

        updated = False
        for k, v in new_mappings.items():
            k_clean = k.lower().strip()
            v_clean = v.strip()
            if custom_kb.get(k_clean) != v_clean:
                custom_kb[k_clean] = v_clean
                self.static_kb[k_clean] = v_clean  # Sync in-memory static_kb as well
                updated = True

        if updated:
            try:
                os.makedirs(os.path.dirname(self.custom_corrections_path), exist_ok=True)
                with open(self.custom_corrections_path, "w", encoding="utf-8") as f:
                    json.dump(custom_kb, f, ensure_ascii=False, indent=2)
                logging.info(f"💾 [TermNormalizer] 增量写入并持久化纠偏词库至: {self.custom_corrections_path}")
            except Exception as e:
                logging.error(f"持久化保存纠偏词库失败: {e}")

    def _parse_json_response(self, response_text):
        """
        Robustly extracts and parses a JSON dictionary from LLM response text,
        handling thinking blocks, markdown wraps, single quotes, and trailing commas.
        """
        if not response_text:
            return {}
            
        clean_text = response_text.strip()
        # 1. Remove thinking block if present
        clean_text = re.sub(r"<think>.*?</think>", "", clean_text, flags=re.DOTALL | re.IGNORECASE).strip()
        
        # 2. Try to capture content inside outer-most curly braces
        start = clean_text.find('{')
        end = clean_text.rfind('}')
        if start != -1 and end != -1 and end > start:
            json_candidate = clean_text[start:end+1]
            try:
                return json.loads(json_candidate)
            except Exception:
                # Lenient replacements: fix trailing commas and single quotes
                fixed_candidate = re.sub(r',\s*}', '}', json_candidate)
                # Replace single quotes only if they appear as JSON structural chars
                # However, a simpler workaround is standard JSON load fallback.
                try:
                    return json.loads(fixed_candidate)
                except Exception:
                    pass

        # 3. Fallback to parsing after stripping markdown code fencing
        try:
            if clean_text.startswith("```json"):
                clean_text = clean_text[7:]
            elif clean_text.startswith("```"):
                clean_text = clean_text[3:]
            if clean_text.endswith("```"):
                clean_text = clean_text[:-3]
            clean_text = clean_text.strip()
            return json.loads(clean_text)
        except Exception as e:
            logging.warning(f"JSON 强力解析器解析失败。原始响应文本: \n{response_text}")
            raise e

    def _filter_unsafe_healed_mappings(self, healed_mappings):
        """
        Reject risky LLM-suggested corrections that downgrade explicit versioned
        model names. OCR often preserves version strings better than ASR or an
        LLM's stale world knowledge, so "V4 Pro" must not be rewritten to "V3"
        just because the verifier guesses from old context.
        """
        if not isinstance(healed_mappings, dict):
            return {}

        safe_mappings = {}
        version_pattern = re.compile(r"\bv\s*(\d+(?:\.\d+)?)\b", re.IGNORECASE)

        def major_version(text):
            match = version_pattern.search(str(text).replace("-", " "))
            if not match:
                return None
            try:
                return float(match.group(1))
            except ValueError:
                return None

        for original, corrected in healed_mappings.items():
            source_version = major_version(original)
            target_version = major_version(corrected)
            if (
                source_version is not None
                and target_version is not None
                and target_version < source_version
            ):
                logging.warning(
                    "⚠️ [TermNormalizer] 拒绝疑似版本降级纠偏: '%s' -> '%s'",
                    original,
                    corrected
                )
                continue
            safe_mappings[original] = corrected

        return safe_mappings

    def normalize(self, transcript_text, dynamic_mapping=None):
        """
        Normalizes the transcript text by replacing ASR typos with correct terms.
        dynamic_mapping: dict of {typo: correct_term} from VisualGrounding.
        Returns:
            canonical_transcript: str
            corrections: list of dicts detailing each correction
        """
        self.corrections_log = []
        if not transcript_text:
            return "", []

        # 1. Merge static KB, dynamic mappings, and loaded custom persistent mappings
        active_kb = self.static_kb.copy()
        if dynamic_mapping:
            for typo, hypothesis in dynamic_mapping.items():
                typo_clean = typo.lower().strip()
                if typo_clean and hypothesis:
                    # Do not allow dynamic guesses to overwrite manually verified
                    # or historical high-precision terms
                    if typo_clean not in active_kb:
                        active_kb[typo_clean] = hypothesis.strip()

        # Sort keys by length descending to ensure longer matches are replaced first
        sorted_typos = sorted(active_kb.keys(), key=len, reverse=True)

        canonical_text = transcript_text

        # 2. Perform safe, boundary-aware replacements
        for typo in sorted_typos:
            correct_term = active_kb[typo]
            
            # Use regex boundaries for clean English alphabetic strings
            if typo.isalnum() and typo.isascii():
                pattern = re.compile(rf"\b{re.escape(typo)}\b", re.IGNORECASE)
            else:
                pattern = re.compile(re.escape(typo), re.IGNORECASE)

            matches = pattern.findall(canonical_text)
            if matches:
                canonical_text = pattern.sub(correct_term, canonical_text)
                logging.info(f"🔄 [TermNormalizer] 纠正 ASR 术语: '{typo}' -> '{correct_term}' (替换了 {len(matches)} 处)")
                self.corrections_log.append({
                    "original_typo": typo,
                    "corrected_term": correct_term,
                    "evidence_source": "VisualGrounding" if dynamic_mapping and typo in [k.lower().strip() for k in dynamic_mapping.keys()] else "LocalStaticKB",
                    "match_count": len(matches),
                    "status": "replaced"
                })

        return canonical_text, self.corrections_log

    def verify_and_heal(self, original_transcript, canonical_text, initial_corrections, api_key, api_base, model):
        """
        Performs a first-pass verification by calling the LLM to inspect the corrected transcript context.
        Note: This checks transcript fragments for immediate grammatical/logical errors.
        """
        if not canonical_text or not initial_corrections:
            return canonical_text, initial_corrections

        logging.info("🩺 [TermNormalizer] 启动转写级前置自愈校验逻辑 (ASR Transcript Verification)...")
        
        system_prompt = (
            "你是一个高级语音纠偏审计与自愈系统。\n"
            "我会给你一段已经进行过初步 ASR 纠偏的文本，以及我们在纠偏中执行的名词映射记录。\n"
            "请仔细审查纠偏后的文本，检查这些修正是否存在逻辑矛盾、技术冲突或不合理的地方。\n"
            "冲突样例：\n"
            "- 如果文本中出现了 'CLAUDE.md' 配置文件，但前文却把工具 'clothcode' 纠偏成了 'Cline'，而 'CLAUDE.md' 是 'Claude Code' / 'Claude' 专有的，这就是一个冲突的纠偏。\n"
            "- 如果文本提到了极低的价格且是国产模型，但把 'DPC V4 Pro' 纠偏成了 'GPT-4o'，这也是一个明显的事实矛盾。\n\n"
            "如果发现冲突，请输出需要修正的正确映射。仅输出一个 JSON 格式的字典，格式如下。如果一切合理没有冲突，请输出空字典 {}。不要说任何废话或 Markdown 围栏。\n"
            "{\n"
            '  "clothcode": "Claude Code",\n'
            '  "cloud code": "Claude Code"\n'
            "}"
        )

        try:
            client = OpenAI(api_key=api_key, base_url=api_base)
            request_kwargs = {
                "model": model,
                "messages": [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": f"【纠偏对照表】：\n{json.dumps(initial_corrections, ensure_ascii=False)}\n\n【初步纠偏文本段落】：\n{canonical_text[:6000]}"}
                ],
                "temperature": 0.0,
                "max_tokens": 1536,
                "stream": False
            }
            
            is_deepseek = "deepseek" in api_base.lower() or "deepseek" in model.lower()
            if is_deepseek:
                request_kwargs["extra_body"] = {"thinking": {"type": "disabled"}}
                
            response = client.chat.completions.create(**request_kwargs)
            response_text = response.choices[0].message.content.strip()
            
            healed_mappings = self._parse_json_response(response_text)
            healed_mappings = self._filter_unsafe_healed_mappings(healed_mappings)
            if isinstance(healed_mappings, dict) and healed_mappings:
                logging.warning(f"⚠️ [TermNormalizer] 转写级审计检测到逻辑冲突！已启动自愈重写映射: {healed_mappings}")
                
                # Dynamic heal and rebuild
                self.save_custom_corrections(healed_mappings)
                final_text, final_corrections = self.normalize(original_transcript, healed_mappings)
                return final_text, final_corrections
            else:
                logging.info("💚 [TermNormalizer] 转写级审计完成：纠偏词在语境中逻辑一致，无需自愈。")
        except Exception as e:
            logging.warning(f"转写自愈审计流程执行异常 (将保留初始纠偏结果): {e}")

        return canonical_text, initial_corrections

    def verify_and_heal_article(self, original_transcript, canonical_text, markdown_content, initial_corrections, api_key, api_base, model):
        """
        Performs a second-pass verification on the final overall article Markdown content,
        detecting technical context/logical conflicts (e.g. mentioning CLAUDE.md but tools corrected to Cline),
        and returning corrected mappings if any error is found.
        """
        if not markdown_content or not initial_corrections:
            return {}

        logging.info("🩺 [TermNormalizer] 启动文章级二次校验与自愈闭环 (Article-Level Verification & Self-Healing Loop)...")
        
        system_prompt = (
            "你是一个高级语音纠偏审计与自愈系统。\n"
            "我会给你一段已经进行过初步 ASR 纠偏并【最终生成后的 Markdown 学习笔记/文章】、我们在前面纠偏中执行的【名词替换记录】以及【原始转写段落】。\n"
            "请仔细审查整篇文章在纠偏后的上下文语境，检查这些名词替换是否存在逻辑矛盾、技术冲突或不合理的地方。\n"
            "核心检查要点：\n"
            "1. 上下文不合理冲突：例如，文章里提到了 CLAUDE.md (这是 Claude Code 的专有配置文件)，但前文却把 ASR 转录的某个词（如 clothcode）纠偏成了 Cline。这显然是错误的纠偏！因为 CLAUDE.md 与 Cline 在技术语境上发生冲突，该词应该被纠偏为 Claude Code。\n"
            "2. 专有名词与实际语境冲突：例如，视频里讲的是 DeepSeek-R1，但前文却把 dipsig 纠偏成了 GPT-4o。\n"
            "3. 词汇关联度：根据文章内提到的专属文件名（如 .cline/config.json, CLAUDE.md）、专属命令或平台，反推前面的语音词是否被错配。\n\n"
            "如果你发现任何错误的纠偏：\n"
            "请识别出【原始语音错误词/发音词】（必须是原始转写中的词或名词替换记录中的 original_typo，或者是 ASR 中被错配的词）与【正确的专有名词】的映射关系。\n"
            "仅以 JSON 字典格式输出需要修正或新增的正确映射关系，例如：\n"
            "{\n"
            '  "clothcode": "Claude Code",\n'
            '  "cloud code": "Claude Code"\n'
            "}\n"
            "如果整篇文章语境完全合理、无技术矛盾，请仅输出一个空 JSON 字典 {}。\n"
            "注意：不要输出任何 markdown 围栏，不要有任何额外的解释文字，只输出符合格式的 JSON 字典。"
        )

        user_content = (
            f"【纠偏对照表】：\n{json.dumps(initial_corrections, ensure_ascii=False, indent=2)}\n\n"
            f"【初步生成的 Markdown 文章全文】：\n{markdown_content[:8000]}\n\n"
            f"【原始转写文本】：\n{original_transcript[:4000]}"
        )

        try:
            client = OpenAI(api_key=api_key, base_url=api_base)
            request_kwargs = {
                "model": model,
                "messages": [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_content}
                ],
                "temperature": 0.0,
                "max_tokens": 1536,
                "stream": False
            }
            
            is_deepseek = "deepseek" in api_base.lower() or "deepseek" in model.lower()
            if is_deepseek:
                request_kwargs["extra_body"] = {"thinking": {"type": "disabled"}}
                
            response = client.chat.completions.create(**request_kwargs)
            response_text = response.choices[0].message.content.strip()
            
            healed_mappings = self._parse_json_response(response_text)
            healed_mappings = self._filter_unsafe_healed_mappings(healed_mappings)
            if isinstance(healed_mappings, dict) and healed_mappings:
                logging.warning(f"⚠️ [TermNormalizer] 文章级审计检测到逻辑冲突！已启动自愈重写映射: {healed_mappings}")
                return healed_mappings
            else:
                logging.info("💚 [TermNormalizer] 文章级审计完成：全文语境逻辑一致，无需自愈。")
        except Exception as e:
            logging.warning(f"文章级自愈审计流程执行异常: {e}")

        return {}

    def repair_timeline_with_article(self, transcript_text, markdown_content, api_key, api_base, model):
        """
        Rewrites the appended transcript timeline after the final article is generated.

        The raw dual-source timeline is useful for synthesis, but it can contain
        low-level ASR/OCR noise. The published wiki should keep the timestamps while
        inheriting the final article's terminology and semantic corrections.
        """
        if not transcript_text or not markdown_content:
            return transcript_text

        logging.info("🧽 [TermNormalizer] 正在用最终文章反向清洗转写时间线，避免保留 ASR/OCR 噪声...")

        system_prompt = (
            "你是技术视频知识库的转写时间线清洗器。\n"
            "我会给你两份内容：\n"
            "1. 已经生成并经过上下文归纳的最终 Markdown 文章；\n"
            "2. ASR + OCR 双源原始时间线，其中可能包含同音字、英文专有名词错写、OCR 噪声、孤立数字、拼写残缺。\n\n"
            "你的任务是输出一份【校正后转写时间线】，用于发布到知识库附录。\n"
            "要求：\n"
            "- 保留原有时间戳顺序，不要编造新时间点。\n"
            "- 结合最终文章修正专有名词、英文大小写、明显同音字和 OCR 噪声。\n"
            "- 删除或合并明显错误的孤立噪声，例如单独的数字、乱码、残缺 billing 拼写。\n"
            "- 不要再输出 ASR/OCR 双源对照标签；每个时间点输出一条清洗后的自然语言摘要或转写。\n"
            "- 不确定的术语可以保留为“疑似/待核实”，但不要留下明显错误原文。\n"
            "- 只输出时间线正文，不要输出 Markdown 代码围栏、解释、前言或结语。\n\n"
            "输出格式示例：\n"
            "- **[00:00:12]** GitHub Copilot 砍掉 Opus 模型，并将在 6 月 1 日全面转向按量计费。\n"
            "- **[00:01:24]** 新的判断框架要看模型能力、速度、额度，以及模型和工具能否解耦。"
        )

        user_content = (
            f"【最终 Markdown 文章，作为术语和语义校正参考】：\n{markdown_content[:12000]}\n\n"
            f"【需要清洗的原始双源时间线】：\n{transcript_text[:24000]}"
        )

        try:
            client = OpenAI(api_key=api_key, base_url=api_base)
            request_kwargs = {
                "model": model,
                "messages": [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_content}
                ],
                "temperature": 0.0,
                "max_tokens": 6000,
                "stream": False
            }

            is_deepseek = "deepseek" in api_base.lower() or "deepseek" in model.lower()
            if is_deepseek:
                request_kwargs["extra_body"] = {"thinking": {"type": "disabled"}}

            response = client.chat.completions.create(**request_kwargs)
            repaired = (response.choices[0].message.content or "").strip()
            repaired = re.sub(r"^```(?:text|markdown)?\s*", "", repaired, flags=re.IGNORECASE)
            repaired = re.sub(r"\s*```$", "", repaired).strip()

            if repaired:
                logging.info("✅ [TermNormalizer] 校正后转写时间线生成完成。")
                return repaired

            logging.warning("校正后转写时间线为空，将保留规范化前时间线。")
        except Exception as e:
            logging.warning(f"转写时间线清洗失败，将保留规范化前时间线: {e}")

        return transcript_text
