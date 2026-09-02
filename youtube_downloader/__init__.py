"""YouTube English subtitle downloader."""

from .captions import choose_best_english_caption
from .downloader import SubtitleDownloader
from .models import DownloadOptions

__all__ = ["DownloadOptions", "SubtitleDownloader", "choose_best_english_caption"]
__version__ = "1.0.0"
