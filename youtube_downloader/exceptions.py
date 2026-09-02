"""Application-specific errors with safe, non-secret messages."""


class YouTubeSubtitleError(Exception):
    """Base error suitable for display to a user."""


class ConfigurationError(YouTubeSubtitleError):
    pass


class CookieError(YouTubeSubtitleError):
    pass


class ExtractionError(YouTubeSubtitleError):
    pass


class CaptionDownloadError(YouTubeSubtitleError):
    pass


class SubtitleError(YouTubeSubtitleError):
    pass


class FilenameError(YouTubeSubtitleError):
    pass


class DownloadCancelled(YouTubeSubtitleError):
    def __init__(self, report_path=None):
        super().__init__("Đã hủy tải xuống an toàn.")
        self.report_path = report_path
