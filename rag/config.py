import os
from dotenv import load_dotenv

load_dotenv()

# OpenAI
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
EMBEDDING_MODEL = "text-embedding-3-small"
EMBEDDING_DIMENSIONS = 1536
LLM_MODEL = "gpt-5.2-chat-latest"

# Chunking
CHUNK_SIZE = 500  # tokens (~2000 chars)
CHUNK_OVERLAP = 50  # tokens (~200 chars)
CHARS_PER_TOKEN = 4  # rough estimate

# Retrieval
TOP_K_RESULTS = 5

# Paths
DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "data")
DOCUMENTS_DIR = os.path.join(DATA_DIR, "documents")
DB_PATH = os.path.join(DATA_DIR, "eggs.db")

# File upload
ALLOWED_EXTENSIONS = {".pdf", ".docx", ".pptx"}
MAX_FILE_SIZE = 50 * 1024 * 1024  # 50MB

# OCR
OCR_MIN_CHARS_PER_PAGE = 50  # if less, assume scanned

# Conversation memory
MAX_HISTORY_MESSAGES = 10  # last N messages included in prompt

# Ensure directories exist
os.makedirs(DOCUMENTS_DIR, exist_ok=True)
