from pathlib import Path
from dotenv import load_dotenv


def load_environment():
    root = Path(__file__).resolve().parents[3]
    load_dotenv(root / ".env", override=False)
