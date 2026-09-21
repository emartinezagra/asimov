import os
import chromadb
import requests
from logging_config import setup_logging

logger = setup_logging()

OLLAMA_URL = "http://127.0.0.1:11434"
EMBED_MODEL = "nomic-embed-text"
OLLAMA_KEEP_ALIVE = os.getenv("OLLAMA_KEEP_ALIVE", "30m")

MEMORY_DB_PATH = os.getenv(
    "MEMORY_DB_PATH",
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "memory_db")
)

client = chromadb.PersistentClient(path=MEMORY_DB_PATH)
collection = client.get_or_create_collection("memory")

def embed(text):
    try:
        resp = requests.post(f"{OLLAMA_URL}/api/embeddings", json={
            "model": EMBED_MODEL, "prompt": text, "keep_alive": OLLAMA_KEEP_ALIVE
        })
        resp.raise_for_status()
        return resp.json()["embedding"]
    except Exception:
        logger.error("Failed to get embedding from Ollama", exc_info=True)
        raise

def add_memory(user_id, text):
    emb = embed(text)
    doc_id = f"{user_id}-{hash(text)}"
    collection.add(documents=[text], embeddings=[emb], ids=[doc_id], metadatas=[{"user_id": str(user_id)}])

def search_memory(user_id, query, k=5):
    emb = embed(query)
    results = collection.query(query_embeddings=[emb], n_results=k, where={"user_id": str(user_id)})
    return results["documents"][0] if results["documents"] else []
