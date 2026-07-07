"""
RAG Chunker — divide docs Markdown em chunks auto-contidos para RAG.

Lê todos os .md de um diretório de docs, divide por seção (##) e gera chunks em JSON.
Cada chunk é auto-contido: carrega o título do documento + heading da seção.

Uso:
    python rag_chunker.py                        # imprime JSON no stdout (docs/ na raiz do projeto)
    python rag_chunker.py --docs-dir docs --out chunks.json
    python rag_chunker.py --min-chars 100         # ignora chunks muito curtos

Parte do plugin docs-maintainer. rag_ingest.py e rag_query.py importam as
funções deste módulo — os três arquivos devem ficar sempre na mesma pasta.
"""

import argparse
import json
import re
import sys
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

ROOT_DIR = Path(__file__).resolve().parent.parent
SKIP_DIRS = {"migrations", "node_modules", ".git", "_site", "site"}
H2_PATTERN = re.compile(r"^## .+", re.MULTILINE)


def resolve_path(value: str, base: Path = ROOT_DIR) -> Path:
    """Resolve um caminho relativo à raiz do projeto (ou absoluto, se já for)."""
    p = Path(value).expanduser()
    return p if p.is_absolute() else (base / p)


def slugify(text: str) -> str:
    text = text.lower().strip()
    text = re.sub(r"[^\w\s-]", "", text)
    text = re.sub(r"[\s_]+", "-", text)
    return re.sub(r"-+", "-", text)


def extract_doc_title(content: str) -> str:
    match = re.search(r"^# (.+)", content, re.MULTILINE)
    return match.group(1).strip() if match else ""


def discover_md_files(docs_dir: Path) -> list[Path]:
    return sorted(
        f for f in docs_dir.rglob("*.md")
        if not any(part in SKIP_DIRS for part in f.parts)
    )


def split_into_chunks(filepath: Path, docs_dir: Path, min_chars: int) -> list[dict]:
    content = filepath.read_text(encoding="utf-8")
    doc_title = extract_doc_title(content)
    source = filepath.relative_to(docs_dir.parent).as_posix()

    splits = list(H2_PATTERN.finditer(content))

    if not splits:
        text = content.strip()
        if len(text) >= min_chars:
            return [{
                "id": f"{filepath.stem}::documento",
                "source": source,
                "doc_title": doc_title,
                "section": doc_title,
                "content": text,
                "chars": len(text),
            }]
        return []

    chunks = []

    # Conteúdo antes do primeiro "## " (título "# ...", texto solto,
    # blockquote de aviso/changelog curto etc.) ficava fora de qualquer
    # chunk — o loop abaixo só cobre a partir de splits[0].start(). Sem
    # isso, esse preâmbulo fica 100% invisível pro RAG mesmo quando é
    # informação relevante (ex.: um resumo/"última atualização" no topo do
    # arquivo). Se o preâmbulo for muito grande (histórico extenso, tipo um
    # changelog inteiro), prefira manter changelog em arquivos separados
    # (um por entrada/data) em vez de um único blockquote gigante no topo —
    # um chunk muito grande e heterogêneo não fica bem representado por um
    # único vetor de embedding, mesmo depois de indexado corretamente.
    preamble = content[:splits[0].start()].strip()
    if len(preamble) >= min_chars:
        chunks.append({
            "id": f"{filepath.stem}::introducao",
            "source": source,
            "doc_title": doc_title,
            "section": doc_title or "Introdução",
            "content": preamble,
            "chars": len(preamble),
        })

    for i, match in enumerate(splits):
        start = match.start()
        end = splits[i + 1].start() if i + 1 < len(splits) else len(content)

        section_text = content[start:end].strip()
        section_heading = match.group(0).lstrip("# ").strip()

        contextualized = f"# {doc_title}\n\n{section_text}" if doc_title else section_text

        if len(section_text) >= min_chars:
            chunks.append({
                "id": f"{filepath.stem}::{slugify(section_heading)}",
                "source": source,
                "doc_title": doc_title,
                "section": section_heading,
                "content": contextualized,
                "chars": len(contextualized),
            })

    return chunks


def chunk_docs(docs_dir: Path, min_chars: int) -> list[dict]:
    all_chunks: list[dict] = []
    for filepath in discover_md_files(docs_dir):
        all_chunks.extend(split_into_chunks(filepath, docs_dir, min_chars))
    return all_chunks


def main():
    parser = argparse.ArgumentParser(description="Gera chunks RAG a partir dos docs markdown de um projeto")
    parser.add_argument("--docs-dir", default="docs",
                        help="Pasta de docs, relativa à raiz do projeto ou absoluta (padrão: docs)")
    parser.add_argument("--out", help="Arquivo de saída JSON (padrão: stdout)")
    parser.add_argument("--min-chars", type=int, default=80, metavar="N",
                        help="Ignora chunks com menos de N caracteres (padrão: 80)")
    args = parser.parse_args()

    docs_dir = resolve_path(args.docs_dir)
    if not docs_dir.exists():
        sys.exit(f"Diretório de docs não encontrado: {docs_dir}")

    all_chunks = chunk_docs(docs_dir, args.min_chars)
    output = json.dumps(all_chunks, ensure_ascii=False, indent=2)

    if args.out:
        Path(args.out).write_text(output, encoding="utf-8")
        print(f"{len(all_chunks)} chunks → {args.out}", file=sys.stderr)
    else:
        print(output)


if __name__ == "__main__":
    main()
