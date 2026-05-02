import chromadb
from langchain_openai import OpenAIEmbeddings
import os

from .. import paths as _paths

api_key = os.getenv("OPENAI_API_KEY")
if api_key:
    embeddings = OpenAIEmbeddings(api_key=api_key)
else:
    embeddings = None

# Env-overridable via EVEREST_CHROMA_DIR; default <workspace>/chroma_db.
client = chromadb.PersistentClient(path=str(_paths.chroma_dir()))

collection = client.get_or_create_collection(name="rfp_documents")

def add_document(doc_id: str, text: str, metadata: dict = None):
    if embeddings:
        vector = embeddings.embed_query(text)
        collection.add(
            ids=[doc_id],
            embeddings=[vector],
            documents=[text],
            metadatas=[metadata] if metadata else None
        )

def search_documents(query: str, n_results: int = 5):
    if embeddings:
        query_vector = embeddings.embed_query(query)
        results = collection.query(
            query_embeddings=[query_vector],
            n_results=n_results
        )
        return results
    else:
        return {"documents": [], "metadatas": []}