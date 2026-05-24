import os
import subprocess
import re
import logging

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

class MediaProcessor:
    def __init__(self, temp_dir):
        self.temp_dir = temp_dir
        os.makedirs(self.temp_dir, exist_ok=True)

    def extract_audio(self, video_path):
        """
        Extract mono 16kHz WAV audio from video. Highly optimized for speech-to-text.
        """
        audio_output = os.path.join(self.temp_dir, "extracted_audio.wav")
        if os.path.exists(audio_output):
            os.remove(audio_output)
            
        logging.info("开始提取音频...")
        # PCM WAV avoids lossy transcoding artifacts before local ASR.
        cmd = [
            "ffmpeg", "-y",
            "-i", video_path,
            "-vn",
            "-ar", "16000",
            "-ac", "1",
            "-c:a", "pcm_s16le",
            audio_output
        ]
        
        subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        logging.info(f"音频提取成功: {audio_output}")
        return audio_output

    def format_seconds(self, seconds_float):
        """
        Convert seconds (float/str) to HH_MM_SS format.
        """
        total_seconds = int(float(seconds_float))
        h = total_seconds // 3600
        m = (total_seconds % 3600) // 60
        s = total_seconds % 60
        return f"{h:02d}_{m:02d}_{s:02d}"

    def extract_candidate_frames(self, video_path, scene_threshold=0.02, max_interval_sec=15, max_width=1280):
        """
        Extract keyframes locally using ffmpeg's scene change detection.
        Combines scene detection + max time interval to prevent missing slides.
        Uses showinfo to parse the exact timestamps of selected frames.
        """
        frames_dir = os.path.join(self.temp_dir, "candidate_frames")
        # Clean existing frames directory
        if os.path.exists(frames_dir):
            import shutil
            shutil.rmtree(frames_dir)
        os.makedirs(frames_dir, exist_ok=True)

        logging.info("开始自适应抽帧分析 (结合场景变动与最大间隔)...")

        # ffmpeg select filter: always include the first frame, then select on
        # scene changes and max interval to avoid missing static slides.
        # scale=max_width:-1 downscales larger images while maintaining aspect ratio
        select_filter = f"select='eq(n,0)+gt(scene,{scene_threshold})+gt(t-prev_selected_t,{max_interval_sec})',showinfo,scale={max_width}:-1"
        output_pattern = os.path.join(frames_dir, "frame_%04d.jpg")

        cmd = [
            "ffmpeg", "-y",
            "-i", video_path,
            "-vf", select_filter,
            "-fps_mode", "vfr",
            "-q:v", "4", # Compression quality (scale 1-31, 2-5 is high quality)
            output_pattern
        ]

        # We run the command and capture stderr to parse the showinfo timestamps
        process = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True)
        stderr_output = []
        
        # Read the stderr stream as it processes to display progress/parse logs
        while True:
            line = process.stderr.readline()
            if not line:
                break
            stderr_output.append(line)
            # Log showinfo lines for visual debugging if needed
            if "showinfo" in line:
                # E.g. [Parsed_showinfo_1 @ 0x...] n:   0 pts:   1523 pts_time:1.523
                logging.debug(line.strip())

        process.wait()
        
        if process.returncode != 0:
            raise RuntimeError(f"FFmpeg 抽帧执行出错，错误码: {process.returncode}")

        # Parse timestamps of selected frames from stderr output
        # Format example: [Parsed_showinfo_1 @ 0x...] n:   0 pts:   1523 pts_time:1.523000
        timestamps = {}
        pattern = re.compile(r"n:\s*(\d+)\s+pts:\s*\d+\s+pts_time:([0-9.]+)")
        
        for line in stderr_output:
            match = pattern.search(line)
            if match:
                frame_idx = int(match.group(1)) # 0-indexed in ffmpeg showinfo
                pts_time = float(match.group(2))
                timestamps[frame_idx + 1] = pts_time # frame_%04d is 1-indexed (frame_0001.jpg maps to n: 0)

        # Rename physical files to self-documenting timestamps
        renamed_files = []
        
        # List actual files in candidate directory to ensure alignment
        actual_files = sorted([f for f in os.listdir(frames_dir) if f.startswith("frame_") and f.endswith(".jpg")])
        
        for file in actual_files:
            # Parse index from file name e.g., frame_0001.jpg -> 1
            idx_match = re.search(r"frame_(\d+)\.jpg", file)
            if idx_match:
                idx = int(idx_match.group(1))
                if idx in timestamps:
                    seconds = timestamps[idx]
                    formatted_time = self.format_seconds(seconds)
                    new_filename = f"frame_{idx:04d}_time_{formatted_time}.jpg"
                    
                    old_path = os.path.join(frames_dir, file)
                    new_path = os.path.join(frames_dir, new_filename)
                    
                    os.rename(old_path, new_path)
                    renamed_files.append((new_filename, formatted_time, seconds))
                else:
                    # Fallback if timestamp not found in logs (assigning approximate time)
                    logging.warning(f"无法确定 frame_{idx:04d}.jpg 的准确时间戳，跳过重命名")

        logging.info(f"抽帧完成，共提取 {len(renamed_files)} 张高清晰度候选截图。")
        return frames_dir, sorted(renamed_files, key=lambda x: x[2])
