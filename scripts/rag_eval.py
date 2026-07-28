"""
RAG Eval — teste de regressão de retrieval para o índice Chroma de um projeto.

Roda uma lista de perguntas com fonte esperada conhecida ("golden queries")
contra o índice já populado por rag_ingest.py, e confere se a fonte esperada
aparece entre os chunks recuperados. Não chama o Claude CLI — só testa a
etapa de retrieval (rápido, determinístico, sem custo de API), que é onde
bugs de indexação/chunking/encoding realmente se manifestam.

Uso:
    python rag_eval.py                              # usa rag_eval.json na raiz do projeto
    python rag_eval.py --fixtures meu_eval.json
    python rag_eval.py --top-k 6 --chroma-path chroma_db --collection meu_projeto_docs

Formato do arquivo de fixtures (lista de objetos JSON):
    [
      {
        "question": "como funciona o endpoint X?",
        "expect_source": "docs/api-rest.md"
      },
      {
        "question": "pergunta com mais de uma fonte válida",
        "expect_source": ["docs/a.md", "docs/b.md"]
      }
    ]

Rode depois de todo rag_ingest.py — é o jeito de descobrir que um chunk
sumiu, um bug de encoding corrompeu o índice, ou uma mudança no chunker
quebrou o retrieval, sem depender de notar por acaso (foi assim que dois
bugs reais de indexação ficaram sem serem percebidos por um tempo num
projeto real antes deste script existir).

Parte do plugin docs-maintainer.
"""

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

# Ver rag_ingest.py — mesmo bug de encoding (cp1252 vs UTF-8 no backend
# SQLite do Chroma em Windows) e mesma correção.
if sys.flags.utf8_mode == 0:
    env = dict(os.environ, PYTHONUTF8="1")
    result = subprocess.run([sys.executable, *sys.argv], env=env)
    sys.exit(result.returncode)

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import chromadb

sys.path.insert(0, str(Path(__file__).resolve().parent))
from rag_chunker import ROOT_DIR, resolve_path, slugify
from rag_embedding import get_embedding_function


def default_collection_name() -> str:
    return f"{slugify(ROOT_DIR.name)}_docs"


def load_fixtures(path: Path) -> list[dict]:
    if not path.exists():
        sys.exit(
            f"Arquivo de fixtures não encontrado: {path}\n"
            "Crie um rag_eval.json na raiz do projeto (ver docstring deste script "
            "pro formato) ou aponte --fixtures pra outro arquivo."
        )
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, list) or not data:
        sys.exit(f"{path} deve conter uma lista JSON não vazia de perguntas.")
    return data


def matches_expected(sources_found: list[str], expected) -> bool:
    expected_list = expected if isinstance(expected, list) else [expected]
    return any(
        any(exp in found for found in sources_found)
        for exp in expected_list
    )


def main():
    parser = argparse.ArgumentParser(
        description="Testa o retrieval do RAG contra perguntas com fonte esperada conhecida"
    )
    parser.add_argument("--fixtures", default="rag_eval.json",
                        help="Arquivo JSON de golden queries, relativo à raiz do projeto ou absoluto (padrão: rag_eval.json)")
    parser.add_argument("--chroma-path", default="chroma_db",
                        help="Pasta do Chroma persistente, relativa à raiz do projeto ou absoluta (padrão: chroma_db)")
    parser.add_argument("--collection", default=None,
                        help="Nome da coleção Chroma (padrão: <nome-da-pasta-do-projeto>_docs)")
    parser.add_argument("--top-k", type=int, default=4,
                        help="Chunks a recuperar por pergunta (padrão: 4, mesmo default de rag_query.py)")
    args = parser.parse_args()

    fixtures_path = resolve_path(args.fixtures)
    chroma_path = resolve_path(args.chroma_path)
    collection_name = args.collection or default_collection_name()

    fixtures = load_fixtures(fixtures_path)

    if not chroma_path.exists():
        sys.exit(f"Chroma não encontrado em {chroma_path}.\nExecute primeiro: python rag_ingest.py")

    # O MESMO embedding da ingestao: avaliar com um modelo um indice gerado por
    # outro compara vetores incomparaveis, e a nota que sai nao significa nada.
    ef = get_embedding_function()
    client = chromadb.PersistentClient(path=str(chroma_path))
    try:
        collection = client.get_collection(collection_name, embedding_function=ef)
    except Exception:
        sys.exit(f"Coleção '{collection_name}' não encontrada no Chroma.\nExecute primeiro: python rag_ingest.py")

    print(f"Rodando {len(fixtures)} golden queries (top-k={args.top_k}) contra '{collection_name}'...\n")

    failures = []
    for i, case in enumerate(fixtures, 1):
        question = case["question"]
        expected = case["expect_source"]

        results = collection.query(
            query_texts=[question],
            n_results=args.top_k,
            include=["metadatas"],
        )
        sources_found = [m["source"] for m in results["metadatas"][0]]

        ok = matches_expected(sources_found, expected)
        status = "OK  " if ok else "FAIL"
        print(f"[{status}] {i}. {question}")
        if not ok:
            print(f"        esperado: {expected}")
            print(f"        recuperado: {sources_found}")
            failures.append(case)

    total = len(fixtures)
    passed = total - len(failures)
    print(f"\n{passed}/{total} passaram.")

    if failures:
        print(
            "\nFalhas indicam regressão de retrieval — chunk sumiu, bug de "
            "encoding, mudança no chunker, ou a pergunta precisa de "
            "reformulação/mais contexto no doc fonte. Rode rag_ingest.py de "
            "novo se os docs mudaram; se persistir, investigue o chunk "
            "direto no Chroma antes de mexer no modelo de embedding."
        )
        sys.exit(1)


if __name__ == "__main__":
    main()
