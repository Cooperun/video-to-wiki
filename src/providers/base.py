from abc import ABC, abstractmethod

class BaseProvider(ABC):
    @abstractmethod
    def generate_wiki(self, transcript_text, candidate_frames, video_title, candidate_frames_dir):
        """
        Generate a highly structured Markdown article from a video.
        
        Args:
            transcript_text (str): Pre-formatted string transcript with timestamps.
            candidate_frames (list): List of tuples: [(filename, formatted_time_string, seconds_float)]
            video_title (str): Sanitized title of the video.
            candidate_frames_dir (str): Absolute path to the candidate frames directory.
            
        Returns:
            tuple[str, list]:
                - Generated Markdown content (str).
                - A list of dictionary objects representing chosen keyframes, e.g.:
                  [{"filename": "frame_0002_time_00_00_42.jpg", "timestamp": "00:00:42"}]
        """
        pass
