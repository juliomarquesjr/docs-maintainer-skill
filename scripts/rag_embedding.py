"""
Função de embedding COMPARTILHADA pelo pipeline RAG (rag_ingest / rag_search /
rag_query). Ponto único de verdade do modelo: ingestão e busca DEVEM usar o mesmo
embedding, senão os vetores não são comparáveis.

Modelo: `paraphrase-multilingual-MiniLM-L12-v2` (sentence-transformers) — 50+
idiomas, forte em português, 384 dimensões. Ao contrário dos modelos `e5`, não
exige prefixos `query:`/`passage:`, então integra direto no pipeline atual.

Trocar de modelo exige RE-INGERIR do zero:
    .venv-docs/bin/python scripts/rag_ingest.py --reset

Pode-se sobrescrever via variável de ambiente `RAG_EMBED_MODEL`.
"""

import os

from chromadb.utils.embedding_functions import SentenceTransformerEmbeddingFunction

EMBED_MODEL = os.environ.get("RAG_EMBED_MODEL", "paraphrase-multilingual-MiniLM-L12-v2")

# Dimensão do vetor do modelo padrão (paraphrase-multilingual-MiniLM-L12-v2 = 384).
# Registrada no rag.lock.json apenas como metadado informativo; não é recomputada
# a partir do modelo para não pagar o carregamento dos pesos só para gerar o lock.
EMBED_DIMS = 384

# Espaço de distância da coleção Chroma (mesmo valor usado no rag_ingest.py).
EMBED_SPACE = "cosine"


def get_embedding_function():
    """Instancia a embedding function multilíngue usada por todo o pipeline RAG."""
    return SentenceTransformerEmbeddingFunction(model_name=EMBED_MODEL)
