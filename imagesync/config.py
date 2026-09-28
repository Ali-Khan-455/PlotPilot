"""Image-Sync settings. The module → sub-style / colour maps live only here (user decision C3); they are
lookup keys into prompts/image-sync-v3.md, never prompt text."""

from pathlib import Path

from plotpilot import config as pp_config

PLOTPILOT_DB = "plotpilot.db"
DB_PATH = "imagesync.db"
SPEC_PATH = Path(__file__).resolve().parent.parent / "prompts" / "image-sync-v3.md"
LOG_DIR = "logs"

ASPECTS = ("16:9", "9:16", "1:1", "4:5")
DEFAULT_ASPECT = "16:9"

MODULE_TO_SUBSTYLE = {"A": "c", "B": "b", "C": "a", "D": "d"}
MODULE_TO_COLOR = {"A": "Isekai/power fantasy", "B": "Romance/drama", "C": "Dark action/revenge",
                   "D": "Comedy/slice of life"}

# Each beat restates its slice of the chunk's narration, so this needs to be draft-sized, not a smaller
# custom constant.
BEATS_MAX_TOKENS = pp_config.GEN_MAX_TOKENS
STAGE1_MAX_TOKENS = pp_config.GEN_MAX_TOKENS
# Stage 2 batches are drafting work (Sonnet), matching Stage 0/1's own size.
STAGE2_MAX_TOKENS = pp_config.GEN_MAX_TOKENS
# The end-of-Stage-2 continuity call's own output is a short JSON list of log entries, not full prose.
BIBLE_UPDATE_MAX_TOKENS = pp_config.QC_MAX_TOKENS

# New batches only -- resume never depends on this (each stage2 pass stores its own start/count/batch_index).
BATCH_SIZE = 30

IMAGES_DIR = "images"
REFS_PENDING_DIR = "refs"
CONTINUITY_PENDING_DIR = "continuity"
BIBLE_DIR = "bibles"
