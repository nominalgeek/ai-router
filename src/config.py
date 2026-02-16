"""Configuration, environment variables, and prompt loading."""

import os
import logging
from datetime import datetime
from zoneinfo import ZoneInfo

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger('ai_router')

# Model endpoints
ROUTER_URL = os.getenv('ROUTER_URL', 'http://router:8001')
PRIMARY_URL = os.getenv('PRIMARY_URL', 'http://primary:8000')
XAI_API_KEY = os.getenv('XAI_API_KEY', '')
XAI_API_URL = 'https://api.x.ai'  # Base URL without /v1
# Available models: grok-4-1-fast-non-reasoning, grok-4-1-fast-reasoning, grok-code-fast-1
XAI_MODEL = os.getenv('XAI_MODEL', 'grok-4-1-fast-reasoning')
ROUTER_MODEL = os.getenv('ROUTER_MODEL', 'nvidia/Nemotron-Mini-4B-Instruct')
PRIMARY_MODEL = os.getenv('PRIMARY_MODEL', 'unsloth/NVIDIA-Nemotron-3-Nano-30B-A3B-NVFP4')

# xAI search tools for enrichment (comma-separated: "web_search,x_search" or "" to disable)
XAI_SEARCH_TOOLS = os.getenv('XAI_SEARCH_TOOLS', 'web_search,x_search')

# How many characters of prior conversation the classifier sees when resolving
# references in follow-up queries. Higher = better context resolution but uses
# more of the router model's context window. ~4 chars ≈ 1 token.
CLASSIFY_CONTEXT_BUDGET = int(os.getenv('CLASSIFY_CONTEXT_BUDGET', '4000'))

# Timezone configuration (defaults to US Pacific / Happy Valley, OR)
LOCAL_TZ = ZoneInfo(os.getenv('TZ', 'America/Los_Angeles'))


def now():
    """Return the current timezone-aware datetime."""
    return datetime.now(LOCAL_TZ)

# Prompt file paths
ROUTING_PROMPT_PATH = os.getenv('ROUTING_PROMPT_PATH', '/app/config/prompts/routing/request.md')
ROUTING_SYSTEM_PROMPT_PATH = os.getenv('ROUTING_SYSTEM_PROMPT_PATH', '/app/config/prompts/routing/system.md')
ENRICHMENT_SYSTEM_PROMPT_PATH = os.getenv('ENRICHMENT_SYSTEM_PROMPT_PATH', '/app/config/prompts/enrichment/system.md')
ENRICHMENT_INJECTION_PROMPT_PATH = os.getenv('ENRICHMENT_INJECTION_PROMPT_PATH', '/app/config/prompts/enrichment/injection.md')
META_SYSTEM_PROMPT_PATH = os.getenv('META_SYSTEM_PROMPT_PATH', '/app/config/prompts/meta/system.md')


def load_prompt_file(path, fallback, label):
    """Load a prompt template from an external markdown file."""
    try:
        with open(path, 'r') as f:
            prompt = f.read().strip()
            logger.info(f"Loaded {label} from {path}")
            return prompt
    except FileNotFoundError:
        logger.error(f"{label} not found at {path}, using fallback")
        return fallback


ROUTING_SYSTEM_PROMPT = load_prompt_file(
    ROUTING_SYSTEM_PROMPT_PATH,
    'You are a query classifier. Respond with ONLY ONE WORD: SIMPLE, MODERATE, or COMPLEX.',
    'routing system prompt'
)

ROUTING_PROMPT = load_prompt_file(
    ROUTING_PROMPT_PATH,
    ('Classify this query as SIMPLE, MODERATE, COMPLEX, or ENRICH.\n'
     'User query: "{query}"\n'
     'Respond with ONLY ONE WORD: SIMPLE, MODERATE, COMPLEX, or ENRICH'),
    'routing prompt'
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
