"""
RAG Query — consulta os docs de um projeto (via Chroma) e responde usando o Claude CLI.

Uso:
    python rag_query.py "como o sistema funciona?"
    python rag_query.py "quais tabelas existem?" --top-k 5
    python rag_query.py "pergunta" --show-sources
    python rag_query.py "pergunta" --chroma-path chroma_db --collection meu_projeto_docs

Requer:
    claude CLI instalado e autenticado (claude --version para verificar)
    Chroma populado (rode rag_ingest.py antes)

Parte do plugin docs-maintainer.
"""

import argparse
import shutil
import subprocess
import sys
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import chromadb

sys.path.insert(0, str(Path(__file__).resolve().parent))
from rag_chunker import ROOT_DIR, resolve_path, slugify
from rag_embedding import get_embedding_function

PROMPT_TEMPLATE = """\
Responda a seguinte pergunta sobre o projeto {project_name} com base exclusivamente \
no contexto fornecido abaixo. Se a resposta não estiver no contexto, diga: \
"Não encontrei essa informação na documentação." \
Seja direto e técnico. Responda em português.

PERGUNTA: {question}

CONTEXTO DA DOCUMENTAÇÃO:
{context}
"""


def _find_claude_exe() -> str:
    cmd_path = shutil.which("claude")
    if not cmd_path:
        sys.exit(
            "Erro: 'claude' não encontrado no PATH.\n"
            "Instale o Claude Code: https://claude.ai/code"
        )

    if cmd_path.upper().endswith(".CMD"):
        npm_dir = Path(cmd_path).parent
        exe = npm_dir / "node_modules" / "@anthropic-ai" / "claude-code" / "bin" / "claude.exe"
        if exe.exists():
            return str(exe)

    return cmd_path


def call_claude(prompt: str) -> str:
    claude_exe = _find_claude_exe()

    result = subprocess.run(
        [claude_exe, "--print", prompt],
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=120,
        cwd=str(Path.home()),
    )

    if result.returncode != 0:
        sys.exit(f"Erro ao chamar o claude CLI:\n{result.stderr.strip()}")

    return result.stdout.strip()


def build_context(results: dict) -> tuple[str, list[dict]]:
    chunks = [
        {"document": doc, "metadata": meta, "distance": dist}
        for doc, meta, dist in zip(
            results["documents"][0],
            results["metadatas"][0],
            results["distances"][0],
        )
    ]

    parts = []
    for i, chunk in enumerate(chunks, 1):
        header = (
            f"[{i}] {chunk['metadata']['doc_title']} "
            f"— {chunk['metadata']['section']}"
        )
        parts.append(f"{header}\n{chunk['document']}")

    return "\n\n---\n\n".join(parts), chunks


def default_collection_name() -> str:
    return f"{slugify(ROOT_DIR.name)}_docs"


def main():
    parser = argparse.ArgumentParser(
        description="Consulta a documentação de um projeto via RAG + Claude CLI"
    )
    parser.add_argument("question", help="Pergunta sobre o projeto")
    parser.add_argument("--chroma-path", default="chroma_db",
                        help="Pasta do Chroma persistente, relativa à raiz do projeto ou absoluta (padrão: chroma_db)")
    parser.add_argument("--collection", default=None,
                        help="Nome da coleção Chroma (padrão: <nome-da-pasta-do-projeto>_docs)")
    parser.add_argument("--project-name", default=None,
                        help="Nome do projeto usado no prompt (padrão: nome da pasta raiz)")
    parser.add_argument("--top-k", type=int, default=4,
                        help="Chunks a recuperar do Chroma (padrão: 4)")
    parser.add_argument("--show-sources", action="store_true",
                        help="Exibe as fontes utilizadas antes da resposta")
    args = parser.parse_args()

    chroma_path = resolve_path(args.chroma_path)
    collection_name = args.collection or default_collection_name()
    project_name = args.project_name or ROOT_DIR.name

    if not chroma_path.exists():
        sys.exit(
            f"Chroma não encontrado em {chroma_path}.\n"
            "Execute primeiro: python rag_ingest.py"
        )

    ef = get_embedding_function()
    client = chromadb.PersistentClient(path=str(chroma_path))

    try:
        collection = client.get_collection(collection_name, embedding_function=ef)
    except Exception:
        sys.exit(
            f"Coleção '{collection_name}' não encontrada no Chroma.\n"
            "Execute primeiro: python rag_ingest.py"
        )

    results = collection.query(
        query_texts=[args.question],
        n_results=args.top_k,
        include=["documents", "metadatas", "distances"],
    )

    context, chunks = build_context(results)

    if args.show_sources:
        print("\nFontes utilizadas:")
        for i, c in enumerate(chunks, 1):
            score = 1 - c["distance"]
            print(
                f"  [{i}] {c['metadata']['source']} "
                f"— {c['metadata']['section']} "
                f"(relevância: {score:.0%})"
            )
        print()

    prompt = PROMPT_TEMPLATE.format(question=args.question, context=context, project_name=project_name)

    print(call_claude(prompt))


if __name__ == "__main__":
    main()
