"""Single place for tunable constants and model IDs."""

from pathlib import Path

# Current aliases per Anthropic's published model table (not verified against the live API);
# the first real run checks them with models.retrieve. Override with --gen-model / --qc-model.
GEN_MODEL = "claude-sonnet-5"
QC_MODEL = "claude-haiku-4-5"
GEN_CONTEXT_TOKENS = 1_000_000
TOKENS_PER_WORD = 1.35

MAX_CHAPTERS = 5
MAX_WORDS = 12_000
MIN_CHAPTER_WORDS = 50
TOC_LINE_RATIO = 0.5

DB_PATH = "plotpilot.db"

GEN_MAX_TOKENS = 64_000
REPAIR_MAX_TOKENS = 8_000
CLASSIFY_MAX_TOKENS = 16
CLASSIFY_WORDS = 2_000

SPEC_PATH = Path(__file__).resolve().parent.parent / "prompts" / "v4-spec.md"
LOG_DIR = "logs"

EMPTY_TRACKER = "None yet (this is the first chunk)."
QC_MAX_TOKENS = 16_000
NORMALIZE_MAX_TOKENS = 32_000
MIN_REWRITE_RATIO = 0.8
