#!/usr/bin/env python3
"""
test_local_ocr.py - 本地 OCR 精度与性能验证测试脚本

功能：
  1. 从目标视频中提取若干采样帧（字幕区域）
  2. 分别在 local / cloud / hybrid 三种模式下运行字幕提取
  3. 输出精度对比报告和速度对比

用法：
  python3 test_local_ocr.py --video /path/to/video.mp4
  python3 test_local_ocr.py --frames-only   # 只生成采样帧，不运行对比
"""

import os
import sys
import time
import argparse
import logging
import subprocess

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("LocalOCRTest")


def extract_test_frames(video_path: str, out_dir: str, interval_sec: float = 10.0, max_frames: int = 20) -> list:
    """从视频中等间距提取测试帧。"""
    os.makedirs(out_dir, exist_ok=True)
    output_pattern = os.path.join(out_dir, "test_frame_%04d.jpg")

    cmd = [
        "ffmpeg", "-y",
        "-i", video_path,
        "-vf", f"fps=1/{interval_sec},scale=1280:-1",
        "-fps_mode", "vfr",
        "-q:v", "3",
        output_pattern
    ]

    logger.info(f"📸 正在从视频提取测试帧（每 {interval_sec}s 一帧，最多 {max_frames} 帧）...")
    result = subprocess.run(cmd, capture_output=True, text=True)

    frames = sorted([
        os.path.join(out_dir, f)
        for f in os.listdir(out_dir)
        if f.startswith("test_frame_") and f.endswith(".jpg")
    ])[:max_frames]

    logger.info(f"✅ 共提取 {len(frames)} 帧")
    return frames


def run_local_ocr_test(frames: list) -> list:
    """在所有采样帧上运行本地 OCR，返回结果列表。"""
    from src.local_ocr import LocalOCRBackend
    from src.subtitle_region import SubtitleRegionDetector

    backend = LocalOCRBackend()
    region = SubtitleRegionDetector().detect_from_paths(frames)
    subtitle_box = region.box if region else None
    if region:
        logger.info(f"📐 [LOCAL ] 自动定位字幕区域: box={subtitle_box}, confidence={region.confidence:.2f}")
    else:
        logger.info("📐 [LOCAL ] 未能自动定位字幕区域，将使用默认下方裁剪框。")

    results = []
    for frame_path in frames:
        t0 = time.time()
        text, confidence = backend.recognize(frame_path, box=subtitle_box)
        elapsed = time.time() - t0
        results.append({
            "frame": os.path.basename(frame_path),
            "text": text,
            "confidence": confidence,
            "elapsed_ms": int(elapsed * 1000),
        })
        status = "✅" if text != "None" else "⬜"
        logger.info(f"  {status} [LOCAL ] {os.path.basename(frame_path)}: '{text}' (conf={confidence:.3f}, {int(elapsed*1000)}ms)")

    return results


def run_cloud_ocr_test(frames: list, api_key: str, api_base: str, model: str) -> list:
    """在所有采样帧上运行云端 Qwen-VL OCR，返回结果列表。"""
    from src.pure_subtitle_extractor import VisualSubtitleExtractor
    extractor = VisualSubtitleExtractor(
        api_key=api_key,
        api_base=api_base,
        model=model,
        ocr_mode="cloud",
    )

    results = []
    for frame_path in frames:
        t0 = time.time()
        text = extractor.ocr_subtitle_cloud(frame_path)
        elapsed = time.time() - t0
        results.append({
            "frame": os.path.basename(frame_path),
            "text": text,
            "confidence": 1.0 if text != "None" else 0.0,
            "elapsed_ms": int(elapsed * 1000),
        })
        status = "✅" if text != "None" else "⬜"
        logger.info(f"  {status} [CLOUD ] {os.path.basename(frame_path)}: '{text}' ({int(elapsed*1000)}ms)")

    return results


def compute_accuracy_vs_reference(local_results: list, cloud_results: list) -> dict:
    """以 Qwen-VL 云端结果为黄金标准，计算本地 OCR 的精度指标。"""
    total = len(cloud_results)
    exact_match = 0
    fuzzy_match = 0
    local_detected = 0
    cloud_detected = 0

    mismatches = []

    for local, cloud in zip(local_results, cloud_results):
        cloud_text = cloud["text"]
        local_text = local["text"]

        if cloud_text != "None":
            cloud_detected += 1
        if local_text != "None":
            local_detected += 1

        # Exact match
        if cloud_text == local_text:
            exact_match += 1
            continue

        # Fuzzy match: check if local is a substring or has high character overlap
        if cloud_text != "None" and local_text != "None":
            # Calculate character-level overlap
            cloud_chars = set(cloud_text)
            local_chars = set(local_text)
            overlap = len(cloud_chars & local_chars) / max(len(cloud_chars), 1)
            if overlap > 0.7:
                fuzzy_match += 1
            else:
                mismatches.append({
                    "frame": cloud["frame"],
                    "cloud": cloud_text,
                    "local": local_text,
                    "local_confidence": local["confidence"],
                })
        elif cloud_text == "None" and local_text == "None":
            exact_match += 1  # Both correctly identify no subtitle
        else:
            mismatches.append({
                "frame": cloud["frame"],
                "cloud": cloud_text,
                "local": local_text,
                "local_confidence": local["confidence"],
            })

    return {
        "total_frames": total,
        "cloud_detected": cloud_detected,
        "local_detected": local_detected,
        "exact_match": exact_match,
        "fuzzy_match": fuzzy_match,
        "exact_accuracy": (exact_match / total * 100) if total > 0 else 0,
        "fuzzy_accuracy": ((exact_match + fuzzy_match) / total * 100) if total > 0 else 0,
        "mismatches": mismatches,
    }


def format_report(local_results: list, cloud_results: list, accuracy: dict) -> str:
    """格式化输出对比报告。"""
    local_avg_ms = sum(r["elapsed_ms"] for r in local_results) / max(len(local_results), 1)
    cloud_avg_ms = sum(r["elapsed_ms"] for r in cloud_results) / max(len(cloud_results), 1)
    speedup = cloud_avg_ms / max(local_avg_ms, 1)

    lines = [
        "",
        "=" * 60,
        "  🧪 本地 OCR vs 云端 Qwen-VL 精度与速度对比报告",
        "=" * 60,
        f"  📊 总帧数: {accuracy['total_frames']}",
        f"  ☁️  云端检测到字幕的帧: {accuracy['cloud_detected']}",
        f"  💻 本地检测到字幕的帧: {accuracy['local_detected']}",
        "",
        f"  🎯 精确匹配率: {accuracy['exact_accuracy']:.1f}% ({accuracy['exact_match']}/{accuracy['total_frames']})",
        f"  🎯 模糊匹配率 (含部分正确): {accuracy['fuzzy_accuracy']:.1f}%",
        "",
        f"  ⚡ 本地平均耗时: {local_avg_ms:.0f} ms/帧",
        f"  ⚡ 云端平均耗时: {cloud_avg_ms:.0f} ms/帧",
        f"  🚀 本地速度倍数: {speedup:.1f}×（相对云端）",
    ]

    if accuracy["mismatches"]:
        lines.append("")
        lines.append(f"  ⚠️  不一致结果 ({len(accuracy['mismatches'])} 帧):")
        for mm in accuracy["mismatches"][:10]:  # 最多显示 10 条
            lines.append(f"    [{mm['frame']}]")
            lines.append(f"      ☁️  云端: {mm['cloud']}")
            lines.append(f"      💻 本地: {mm['local']} (conf={mm['local_confidence']:.3f})")

    lines.append("=" * 60)
    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(description="本地 OCR vs 云端 OCR 精度与速度对比测试")
    parser.add_argument("--video", type=str, help="视频文件路径（用于提取测试帧）")
    parser.add_argument("--frames-dir", type=str, default="./temp/ocr_test_frames", help="测试帧目录")
    parser.add_argument("--interval", type=float, default=10.0, help="抽帧间隔（秒），默认 10")
    parser.add_argument("--max-frames", type=int, default=20, help="最多测试帧数，默认 20")
    parser.add_argument("--local-only", action="store_true", help="只测试本地 OCR，不调用云端")
    parser.add_argument("--cloud-only", action="store_true", help="只测试云端 OCR，不测本地")
    args = parser.parse_args()

    # 加载配置以获取 API Key
    try:
        from src.config import AppConfig
        config = AppConfig()
    except Exception as e:
        logger.error(f"配置加载失败: {e}")
        sys.exit(1)

    # 确定测试帧来源
    frames = []
    if args.video:
        if not os.path.exists(args.video):
            logger.error(f"视频文件不存在: {args.video}")
            sys.exit(1)
        frames = extract_test_frames(args.video, args.frames_dir, args.interval, args.max_frames)
    else:
        # 尝试从 temp 目录中读取已有帧
        if os.path.exists(args.frames_dir):
            frames = sorted([
                os.path.join(args.frames_dir, f)
                for f in os.listdir(args.frames_dir)
                if f.endswith(".jpg") and "_roi" not in f
            ])[:args.max_frames]

    if not frames:
        logger.error("没有找到测试帧。请使用 --video /path/to/video.mp4 指定视频文件。")
        sys.exit(1)

    logger.info(f"🎬 找到 {len(frames)} 张测试帧，开始 OCR 对比测试...")

    # 运行本地 OCR
    local_results = []
    if not args.cloud_only:
        logger.info("\n💻 [Step 1/2] 运行本地 OCR...")
        local_results = run_local_ocr_test(frames)
    
    # 运行云端 OCR
    cloud_results = []
    if not args.local_only:
        if not config.qwen_api_key:
            logger.warning("未找到 Qwen API Key，跳过云端 OCR 测试。")
        else:
            logger.info("\n☁️  [Step 2/2] 运行云端 Qwen-VL OCR...")
            cloud_results = run_cloud_ocr_test(
                frames,
                api_key=config.qwen_api_key,
                api_base=config.qwen_api_base,
                model=config.qwen_model,
            )

    # 生成对比报告
    if local_results and cloud_results and len(local_results) == len(cloud_results):
        accuracy = compute_accuracy_vs_reference(local_results, cloud_results)
        report = format_report(local_results, cloud_results, accuracy)
        print(report)

        # 保存报告
        report_path = os.path.join(args.frames_dir, "ocr_comparison_report.txt")
        with open(report_path, "w", encoding="utf-8") as f:
            f.write(report)
        logger.info(f"📄 报告已保存至: {report_path}")
    elif local_results:
        logger.info(f"\n💻 本地 OCR 测试完成，共识别 {sum(1 for r in local_results if r['text'] != 'None')}/{len(local_results)} 帧有字幕。")
        avg_ms = sum(r["elapsed_ms"] for r in local_results) / max(len(local_results), 1)
        logger.info(f"⚡ 平均耗时: {avg_ms:.0f} ms/帧")
    elif cloud_results:
        logger.info(f"\n☁️ 云端 OCR 测试完成，共识别 {sum(1 for r in cloud_results if r['text'] != 'None')}/{len(cloud_results)} 帧有字幕。")


if __name__ == "__main__":
    main()
