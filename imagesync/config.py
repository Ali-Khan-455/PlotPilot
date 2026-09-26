"""Image-Sync settings. The module → sub-style / colour maps live only here (user decision C3); they are
lookup keys into prompts/image-sync-v3.md, never prompt text."""

from pathlib import Path

PLOTPILOT_DB = "plotpilot.db"
DB_PATH = "imagesync.db"
SPEC_PATH = Path(__file__).resolve().parent.parent / "prompts" / "image-sync-v3.md"
LOG_DIR = "logs"

ASPECTS = ("16:9", "9:16", "1:1", "4:5")
DEFAULT_ASPECT = "16:9"

MODULE_TO_SUBSTYLE = {"A": "c", "B": "b", "C": "a", "D": "d"}
MODULE_TO_COLOR = {"A": "Isekai/power fantasy", "B": "Romance/drama", "C": "Dark action/revenge",
                   "D": "Comedy/slice of life"}
