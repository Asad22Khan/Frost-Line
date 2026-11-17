"""SOP knowledge base: a Pinecone index with Pinecone-hosted embeddings and reranking.

Pinecone embeds the text on its own servers (integrated inference), so no embedding model has to run
locally or on the app server, and ingestion and search can never use mismatched embeddings.
"""

from functools import lru_cache

from pinecone import Pinecone

from src import config

INDEX_NAME = "frostline-sop"
NAMESPACE = "sop"
EMBED_MODEL = "llama-text-embed-v2"
RERANK_MODEL = "bge-reranker-v2-m3"
TEXT_FIELD = "chunk_text"  # the record field Pinecone embeds
CLOUD, REGION = "aws", "us-east-1"  # free Starter plan region

RESULT_FIELDS = [TEXT_FIELD, "source_file", "section", "file_format"]

# Reranker relevance (0-1) below which a chunk is treated as "not about this question". Returning nothing
# lets the agent say the SOP doesn't cover a topic instead of citing an unrelated clause.
MIN_RERANK_SCORE = 0.05


@lru_cache(maxsize=1)
def _client() -> Pinecone:
    return Pinecone(api_key=config.require("PINECONE_API_KEY"))


def index_exists() -> bool:
    return _client().has_index(INDEX_NAME)


def get_index(create_if_missing: bool = False):
    pc = _client()
    if not pc.has_index(INDEX_NAME):
        if not create_if_missing:
            raise RuntimeError(f"Pinecone index '{INDEX_NAME}' not found. Run: python scripts/ingest_sop_pinecone.py")
        print(f"Creating Pinecone index '{INDEX_NAME}' ({EMBED_MODEL}, {CLOUD}/{REGION}) ...")
        pc.create_index_for_model(
            name=INDEX_NAME,
            cloud=CLOUD,
            region=REGION,
            embed={"model": EMBED_MODEL, "field_map": {"text": TEXT_FIELD}},
        )
    return pc.Index(INDEX_NAME)


def search_sop(query: str, top_k: int = 3) -> list[dict]:
    """Semantic search over the SOPs. Fetches extra candidates, then reranks them for precision.

    Returns a list of {"id", "score", "text", "source_file", "section", "file_format"}.
    """
    index = get_index()
    request = {"namespace": NAMESPACE, "inputs": {"text": query}, "top_k": top_k * 3, "fields": RESULT_FIELDS}
    reranked = True
    try:
        response = index.search(**request, rerank={"model": RERANK_MODEL, "rank_fields": [TEXT_FIELD], "top_n": top_k})
    except Exception as exc:  # reranker quota or outage: fall back to plain vector ranking
        print(f"Reranker unavailable ({exc.__class__.__name__}); using vector ranking only.")
        response = index.search(**{**request, "top_k": top_k})
        reranked = False

    results = []
    for hit in response.result.hits:
        if reranked and hit.score < MIN_RERANK_SCORE:
            continue
        fields = hit.fields or {}
        results.append(
            {
                "id": hit.id,
                "score": round(hit.score, 4),
                "text": fields.get(TEXT_FIELD, ""),
                "source_file": fields.get("source_file", "unknown"),
                "section": fields.get("section", ""),
                "file_format": fields.get("file_format", ""),
            }
        )
    return results
