"""
RAG Ingest — popula um Chroma local com os docs markdown de um projeto.

Uso:
    pip install -r requirements.txt
    python rag_ingest.py                 # indexa/atualiza os docs (docs/ na raiz do projeto)
    python rag_ingest.py --reset         # recria a coleção do zero
    python rag_ingest.py --docs-dir docs --chroma-path chroma_db --collection meu_projeto_docs

Parte do plugin docs-maintainer.
"""

import argparse
import sys
from collections import Counter
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import chromadb

sys.path.insert(0, str(Path(__file__).resolve().parent))
from rag_chunker import ROOT_DIR, resolve_path, slugify, chunk_docs
from rag_embedding import get_embedding_function
from rag_manifest import compute_manifest, write_lock, LOCK_PATH

BATCH_SIZE = 50


def default_collection_name() -> str:
    return f"{slugify(ROOT_DIR.name)}_docs"


def main():
    parser = argparse.ArgumentParser(description="Indexa os docs markdown de um projeto num Chroma local")
    parser.add_argument("--docs-dir", default="docs",
                        help="Pasta de docs, relativa à raiz do projeto ou absoluta (padrão: docs)")
    parser.add_argument("--chroma-path", default="chroma_db",
                        help="Pasta do Chroma persistente, relativa à raiz do projeto ou absoluta (padrão: chroma_db)")
    parser.add_argument("--collection", default=None,
                        help="Nome da coleção Chroma (padrão: <nome-da-pasta-do-projeto>_docs)")
    parser.add_argument("--reset", action="store_true", help="Apaga e recria a coleção do zero")
    parser.add_argument("--min-chars", type=int, default=80,
                        help="Ignora chunks com menos de N caracteres (padrão: 80)")
    parser.add_argument("--no-lock", action="store_true",
                        help="Não atualiza o rag.lock.json ao final (padrão: atualiza)")
    args = parser.parse_args()

    docs_dir = resolve_path(args.docs_dir)
    chroma_path = resolve_path(args.chroma_path)
    collection_name = args.collection or default_collection_name()

    if not docs_dir.exists():
        sys.exit(f"Diretório de docs não encontrado: {docs_dir}")

    print(f"Conectando ao Chroma em {chroma_path} (coleção: {collection_name}) ...")
    client = chromadb.PersistentClient(path=str(chroma_path))

    if args.reset:
        try:
            client.delete_collection(collection_name)
            print("Coleção anterior apagada.")
        except Exception:
            pass

    ef = get_embedding_function()
    collection = client.get_or_create_collection(
        name=collection_name,
        embedding_function=ef,
        metadata={"hnsw:space": "cosine"},
    )

    all_chunks = chunk_docs(docs_dir, args.min_chars)

    if not all_chunks:
        print(f"Nenhum chunk encontrado em {docs_dir}.")
        sys.exit(1)

    counts = Counter(c["source"] for c in all_chunks)
    for source, n in sorted(counts.items()):
        print(f"  {source}: {n} chunks")

    print(f"\nTotal: {len(all_chunks)} chunks. Enviando para o Chroma...")

    ids = [c["id"] for c in all_chunks]
    documents = [c["content"] for c in all_chunks]
    metadatas = [
        {
            "source": c["source"],
            "doc_title": c["doc_title"],
            "section": c["section"],
            "chars": c["chars"],
        }
        for c in all_chunks
    ]

    for i in range(0, len(all_chunks), BATCH_SIZE):
        end = min(i + BATCH_SIZE, len(all_chunks))
        collection.upsert(
            ids=ids[i:end],
            documents=documents[i:end],
            metadatas=metadatas[i:end],
        )
        print(f"  {end}/{len(all_chunks)}")

    print(f"\nOK — {collection.count()} chunks indexados em {chroma_path}")

    # Atualiza o "mapa de indexação" versionado (rag.lock.json). É ele — e não o
    # chroma_db/ binário — que viaja no git e garante que todo dev reconstrua a
    # MESMA indexação. Ver scripts/rag_manifest.py e docs/RAG-INDEXACAO.md.
    if not args.no_lock:
        manifest = compute_manifest(docs_dir, collection=collection_name, min_chars=args.min_chars)
        write_lock(manifest)
        print(f"rag.lock.json atualizado ({manifest['total_chunks']} chunks, {len(manifest['docs'])} docs) → {LOCK_PATH}")

    print('Próximo passo: python rag_query.py "sua pergunta aqui"')


if __name__ == "__main__":
    main()
