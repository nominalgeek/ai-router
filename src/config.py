"""Configuration, environment variables, and prompt loading."""

import os
import logging
from logging.handlers import RotatingFileHandler
from datetime import datetime
from zoneinfo import ZoneInfo

# Configure logging — writes to both stdout and a rotating file.
# The file lives under LOG_DIR (default /var/log/ai-router/) so the
# session-review agent can read application logs alongside session JSONs.
LOG_DIR = os.getenv('LOG_DIR', '/var/log/ai-router')
LOG_FORMAT = '%(asctime)s - %(name)s - %(levelname)s - %(message)s'

logging.basicConfig(level=logging.INFO, format=LOG_FORMAT)
logger = logging.getLogger('ai_router')

# Rotating file handler: 5 MB per file, keep 3 backups (≈20 MB max)
os.makedirs(LOG_DIR, exist_ok=True)
_file_handler = RotatingFileHandler(
    os.path.join(LOG_DIR, 'app.log'),
    maxBytes=5 * 1024 * 1024,
    backupCount=3,
)
_file_handler.setFormatter(logging.Formatter(LOG_FORMAT))
logger.addHandler(_file_handler)

# Model endpoints
ROUTER_URL = os.getenv('ROUTER_URL', 'http://router:8001')
PRIMARY_URL = os.getenv('PRIMARY_URL', 'http://primary:8000')
def read_secret(name, default=''):
    """Read a secret from a Docker secret file, falling back to env var.

    Docker secrets are mounted as files under /run/secrets/. This lets
    sensitive values stay out of environment variables (which are visible
    via `docker inspect`).  When running outside Docker (e.g. local dev),
    the env var fallback keeps things working transparently.
    """
    secret_path = f'/run/secrets/{name.lower()}'
    try:
        with open(secret_path, 'r') as f:
            value = f.read().strip()
            if value:
                logger.info(f"Loaded secret '{name}' from {secret_path}")
                return value
    except FileNotFoundError:
        pass
    return os.getenv(name, default)


XAI_API_KEY = read_secret('XAI_API_KEY')
API_KEY = read_secret('API_KEY')
XAI_API_URL = 'https://api.x.ai'  # Base URL without /v1
# Available models: grok-4-1-fast-non-reasoning, grok-4-1-fast-reasoning, grok-code-fast-1
XAI_MODEL = os.getenv('XAI_MODEL', 'grok-4-1-fast-reasoning')
ROUTER_MODEL = os.getenv('ROUTER_MODEL', 'cyankiwi/Nemotron-Orchestrator-8B-AWQ-4bit')
PRIMARY_MODEL = os.getenv('PRIMARY_MODEL', 'unsloth/NVIDIA-Nemotron-3-Nano-30B-A3B-NVFP4')

# Virtual model name presented to external consumers (e.g. Open WebUI).
# Callers see this single model and the routing is invisible to them.
VIRTUAL_MODEL = os.getenv('VIRTUAL_MODEL', 'ai-router')

# xAI search tools for enrichment (comma-separated: "web_search,x_search" or "" to disable)
XAI_SEARCH_TOOLS = os.getenv('XAI_SEARCH_TOOLS', 'web_search,x_search')

# Client max_tokens handling strategy for the classifier:
#
# ROUTER (local classifier): Strip max_tokens entirely, same rationale as the
# primary model.  The Orchestrator 8B is a reasoning model that wraps its
# decision in <think> blocks.  Any artificial cap risks truncating reasoning
# before the classification word is emitted.  vLLM's --max-model-len (32K,
# same as primary) is the only limit needed — the model emits its label
# and stops.

# Client max_tokens handling strategy:
#
# PRIMARY (local reasoning model): Strip max_tokens entirely.  The Nano 30B
# is a reasoning model that burns thousands of tokens on <think> blocks
# before producing visible content.  Any client-supplied cap risks cutting
# off reasoning mid-stream, resulting in content=null.  vLLM's
# --max-model-len (32K) is the only limit needed — the model stops when
# it emits its natural end token.
#
# XAI (cloud API): Keep a floor.  We pay per token, but the client's
# default (often 100-300 from Open WebUI) truncates substantive answers.
XAI_MIN_MAX_TOKENS = int(os.getenv('XAI_MIN_MAX_TOKENS', '16384'))

# Timezone configuration (defaults to US Pacific / Happy Valley, OR)
LOCAL_TZ = ZoneInfo(os.getenv('TZ', 'America/Los_Angeles'))


def now():
    """Return the current timezone-aware datetime."""
    return datetime.now(LOCAL_TZ)


def date_context():
    """
    Build a rich temporal context string from the current local time.
    Injected into system messages so models can reason about "today",
    "this morning", "the weekend", seasons, etc.

    Example output:
      Today is Saturday, February 15, 2026. It is evening (8:42 PM PST).
      It is a weekend. The current season is winter.
    """
    t = now()

    # Day type
    day_type = 'weekend' if t.weekday() >= 5 else 'weekday'

    # Time of day — coarse bucket the model can use for greetings,
    # "tonight" vs "this morning", etc.
    hour = t.hour
    if hour < 5:
        period = 'late night'
    elif hour < 12:
        period = 'morning'
    elif hour < 17:
        period = 'afternoon'
    elif hour < 21:
        period = 'evening'
    else:
        period = 'night'

    # Season (Northern Hemisphere, meteorological convention)
    month = t.month
    if month in (3, 4, 5):
        season = 'spring'
    elif month in (6, 7, 8):
        season = 'summer'
    elif month in (9, 10, 11):
        season = 'autumn'
    else:
        season = 'winter'

    # Timezone abbreviation (e.g. "PST", "PDT")
    tz_abbr = t.strftime('%Z')

    date_str = t.strftime('%A, %B %d, %Y')          # Saturday, February 15, 2026
    time_str = t.strftime('%-I:%M %p')               # 8:42 PM

    return (
        f"Today is {date_str}. It is {period} ({time_str} {tz_abbr}). "
        f"It is a {day_type}. The current season is {season}."
    )

# Prompt file paths
ROUTING_PROMPT_PATH = os.getenv('ROUTING_PROMPT_PATH', '/app/config/prompts/routing/request.md')
ROUTING_TRUNCATION_NOTE_PATH = os.getenv('ROUTING_TRUNCATION_NOTE_PATH', '/app/config/prompts/routing/truncation_note.md')
ROUTING_SYSTEM_PROMPT_PATH = os.getenv('ROUTING_SYSTEM_PROMPT_PATH', '/app/config/prompts/routing/system.md')
PRIMARY_SYSTEM_PROMPT_PATH = os.getenv('PRIMARY_SYSTEM_PROMPT_PATH', '/app/config/prompts/primary/system.md')
ENRICHMENT_SYSTEM_PROMPT_PATH = os.getenv('ENRICHMENT_SYSTEM_PROMPT_PATH', '/app/config/prompts/enrichment/system.md')
ENRICHMENT_INJECTION_PROMPT_PATH = os.getenv('ENRICHMENT_INJECTION_PROMPT_PATH', '/app/config/prompts/enrichment/injection.md')
META_SYSTEM_PROMPT_PATH = os.getenv('META_SYSTEM_PROMPT_PATH', '/app/config/prompts/meta/system.md')
XAI_SYSTEM_PROMPT_PATH = os.getenv('XAI_SYSTEM_PROMPT_PATH', '/app/config/prompts/xai/system.md')


def load_prompt_file(path, fallback, label):
    """
    Load a prompt template from an external markdown file.

    The fallback string is a hardcoded default that keeps the router
    functional if the prompt file is missing (e.g. running outside
    Docker without the config/ volume mounted).  This is an intentional
    exception to the "no natural-language instructions in Python" rule —
    the authoritative prompts live in config/prompts/, and the fallbacks
    exist only as a safety net so the service degrades gracefully instead
    of crashing.  A log error is emitted whenever a fallback is used.
    """
    try:
        with open(path, 'r') as f:
            prompt = f.read().strip()
            logger.info(f"Loaded {label} from {path}")
            return prompt
    except FileNotFoundError:
        logger.error(f"{label} not found at {path}, using fallback")
        return fallback


# --- Prompt loading ---
# Authoritative prompts live in config/prompts/*.md (see project structure).
# The second argument to each call below is a hardcoded fallback — see the
# load_prompt_file docstring for why these exist despite the externalization rule.

PRIMARY_SYSTEM_PROMPT = load_prompt_file(
    PRIMARY_SYSTEM_PROMPT_PATH,
    'Use this as background context only — do not repeat or display it in your response.',
    'primary system prompt'
)

ROUTING_SYSTEM_PROMPT = load_prompt_file(
    ROUTING_SYSTEM_PROMPT_PATH,
    'You are a query classifier. Respond with ONLY ONE WORD: SIMPLE, MODERATE, or COMPLEX.',
    'routing system prompt'
)

ROUTING_PROMPT = load_prompt_file(
    ROUTING_PROMPT_PATH,
    ('Classify this query as SIMPLE, MODERATE, COMPLEX, or ENRICH.\n'
     'User query: "{query}"\n'
     '{truncation_note}\n'
     'Respond with ONLY ONE WORD: SIMPLE, MODERATE, COMPLEX, or ENRICH'),
    'routing prompt'
)

ROUTING_TRUNCATION_NOTE = load_prompt_file(
    ROUTING_TRUNCATION_NOTE_PATH,
    'Note: The above query was truncated. Classify based on what you can see.',
    'routing truncation note'
)

ENRICHMENT_SYSTEM_PROMPT = load_prompt_file(
    ENRICHMENT_SYSTEM_PROMPT_PATH,
    'You are a real-time information retrieval assistant. Provide concise, factual, current information relevant to the user\'s query. Do not answer the question directly — your output will be used as context for another model.',
    'enrichment system prompt'
)

ENRICHMENT_INJECTION_PROMPT = load_prompt_file(
    ENRICHMENT_INJECTION_PROMPT_PATH,
    'The following is supplementary real-time context retrieved from an external source:\n\n---\n{context}\n---',
    'enrichment injection prompt'
)

META_SYSTEM_PROMPT = load_prompt_file(
    META_SYSTEM_PROMPT_PATH,
    'You are processing a structured task about a prior conversation. Follow the task instructions exactly. Be concise.',
    'meta system prompt'
)

XAI_SYSTEM_PROMPT = load_prompt_file(
    XAI_SYSTEM_PROMPT_PATH,
    'Be direct and concise. Lead with the answer, then provide supporting detail only if it adds clear value.',
    'xai system prompt'
)
