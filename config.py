import os

TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN")
OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY")
OPENAI_BASE_URL = os.environ.get("OPENAI_BASE_URL", "https://ai.externcashpn.cv/v1")
OPENAI_MODEL = os.environ.get("OPENAI_MODEL", "gpt-5.4")
SUPADATA_API_KEY = os.environ.get("SUPADATA_API_KEY")
SUPABASE_URL = os.environ.get("SUPABASE_URL")
SUPABASE_SERVICE_ROLE_KEY = os.environ.get("SUPABASE_SERVICE_ROLE_KEY")
WEB_APP_BASE_URL = os.environ.get("WEB_APP_BASE_URL")
BOT_USERNAME = os.environ.get("BOT_USERNAME")
PORT = int(os.environ.get("PORT", 8080))

CHUNK_TARGET_TOKENS = int(os.environ.get("CHUNK_TARGET_TOKENS", 2500))
CHUNK_MAX_TOKENS = int(os.environ.get("CHUNK_MAX_TOKENS", 3000))
CHUNK_OVERLAP_TOKENS = int(os.environ.get("CHUNK_OVERLAP_TOKENS", 200))
ANALYST_MODEL_SMALL = os.environ.get("ANALYST_MODEL_SMALL", OPENAI_MODEL)
ANALYST_MODEL_LARGE = os.environ.get("ANALYST_MODEL_LARGE", OPENAI_MODEL)
SYNTHESIZER_MODEL = os.environ.get("SYNTHESIZER_MODEL", OPENAI_MODEL)

CLIENT = None
SUPABASE = None

if OPENAI_API_KEY:
    from openai import OpenAI
    CLIENT = OpenAI(api_key=OPENAI_API_KEY, base_url=OPENAI_BASE_URL)

if SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY:
    from supabase import create_client
    SUPABASE = create_client(SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY)
