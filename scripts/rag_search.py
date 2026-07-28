"""
RAG Search — recupera os trechos mais relevantes da documentação (Chroma) SEM
chamar nenhum LLM. Complementa o rag_query.py do plugin docs-maintainer, que
depende do `claude` CLI no PATH.

Use quando o agente/dev quiser só o CONTEXTO recuperado (para sintetizar a
resposta por conta própria) — é o modo portável, sem billing e sem exigir
`claude` instalado na máquina.

Uso:
    python rag_search.py "como funciona o isolamento por tenant?"
    python rag_search.py "quais filas existem?" --top-k 6
    python rag_search.py "pergunta" --json        # saída estruturada p/ agentes
    python rag_search.py "pergunta" --chroma-path chroma_db --collection sdrmedico_docs

Requer: Chroma populado (rode rag_ingest.py antes).
Parte do fluxo de documentação do projeto (ver CLAUDE.md → seção Documentação).
"""

import argparse
import json
import sys
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import chromadb

sys.path.insert(0, str(Path(__file__).resolve().parent))
from rag_chunker import ROOT_DIR, resolve_path, slugify
from rag_embedding import get_embedding_function


def default_collection_name() -> str:
    return f"{slugify(ROOT_DIR.name)}_docs"


def main():
    parser = argparse.ArgumentParser(
        description="Recupera trechos relevantes da doc via RAG (sem LLM)"
    )
    parser.add_argument("question", help="Pergunta / termo de busca")
    parser.add_argument("--chroma-path", default="chroma_db",
                        help="Pasta do Chroma persistente (padrão: chroma_db)")
    parser.add_argument("--collection", default=None,
                        help="Nome da coleção (padrão: <pasta-do-projeto>_docs)")
    parser.add_argument("--top-k", type=int, default=4,
                        help="Quantos trechos recuperar (padrão: 4)")
    parser.add_argument("--json", action="store_true",
                        help="Saída em JSON estruturado (para consumo por agentes)")
    args = parser.parse_args()

    chroma_path = resolve_path(args.chroma_path)
    collection_name = args.collection or default_collection_name()

    if not chroma_path.exists():
        sys.exit(
            f"Chroma não encontrado em {chroma_path}.\n"
            "Execute primeiro: python scripts/rag_ingest.py"
        )

    ef = get_embedding_function()
    client = chromadb.PersistentClient(path=str(chroma_path))

    try:
        collection = client.get_collection(collection_name, embedding_function=ef)
    except Exception:
        sys.exit(
            f"Coleção '{collection_name}' não encontrada no Chroma.\n"
            "Execute primeiro: python scripts/rag_ingest.py"
        )

    results = collection.query(
        query_texts=[args.question],
        n_results=args.top_k,
        include=["documents", "metadatas", "distances"],
    )

    hits = [
        {
            "rank": i + 1,
            "relevance": round(1 - dist, 4),
            "source": meta.get("source"),
            "doc_title": meta.get("doc_title"),
            "section": meta.get("section"),
            "content": doc,
        }
        for i, (doc, meta, dist) in enumerate(zip(
            results["documents"][0],
            results["metadatas"][0],
            results["distances"][0],
        ))
    ]

    if args.json:
        print(json.dumps({"question": args.question, "hits": hits},
                         ensure_ascii=False, indent=2))
        return

    if not hits:
        print("Nenhum trecho relevante encontrado.")
        return

    print(f"PERGUNTA: {args.question}\n")
    print(f"{len(hits)} trechos recuperados:\n")
    for h in hits:
        print(f"[{h['rank']}] {h['source']} — {h['section']} "
              f"(relevância {h['relevance']:.0%})")
        print("-" * 72)
        print(h["content"])
        print()


if __name__ == "__main__":
    main()
