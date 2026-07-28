"""
RAG Chunker — divide docs Markdown em chunks pequenos e auto-contidos para RAG.

Lê todos os .md de um diretório de docs e divide em duas etapas:
  1. por seção (heading `##`);
  2. dentro de cada seção, em SUB-CHUNKS de ~TARGET_CHARS com sobreposição
     (OVERLAP_CHARS) entre blocos vizinhos.

Cada sub-chunk é prefixado com "{título do doc} › {seção}", para que o embedding
tenha o contexto temático mesmo num pedaço do meio da seção. Isso importa porque
o modelo de embedding tem janela curta (o multilíngue MiniLM trunca ~128 tokens)
e só "vê" o início de cada chunk — se o chunk for a seção inteira (centenas de
tokens), o embedding ignora quase tudo. Sub-chunks curtos + prefixo de contexto
resolvem isso. Ver scripts/rag_embedding.py.

Uso:
    python rag_chunker.py                         # imprime JSON no stdout (docs/ na raiz do projeto)
    python rag_chunker.py --docs-dir docs --out chunks.json
    python rag_chunker.py --min-chars 100         # ignora seções muito curtas
    python rag_chunker.py --target-chars 450 --overlap-chars 90

ATENÇÃO: mudar o esquema de chunking muda os ids dos chunks. Re-indexe do zero
(`rag_ingest.py --reset`) para não deixar chunks órfãos no Chroma.

Parte do plugin docs-maintainer (adaptado). rag_ingest.py e rag_query.py importam
as funções deste módulo — os três arquivos devem ficar sempre na mesma pasta.
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

# Versão do ESQUEMA de chunking. Faça bump (+1) sempre que mudar o algoritmo de
# fatiamento ou o formato dos ids/prefixos (qualquer coisa que altere os chunks
# gerados para um mesmo doc). O rag.lock.json grava este número; rag_verify.py
# acusa "fora de sync" quando o lock foi gerado por um esquema diferente do atual,
# forçando um re-ingest (`rag_ingest.py --reset`) em vez de comparar chunks
# incompatíveis. Ver scripts/rag_manifest.py.
SCHEMA_VERSION = 1

# Tamanho-alvo do CORPO de cada sub-chunk (sem o prefixo de contexto). ~450 chars
# ≈ ~120 tokens em PT, para caber na janela do modelo de embedding multilíngue.
TARGET_CHARS = 450
# Sobreposição textual (em chars) reaproveitada no início do bloco seguinte, para
# não cortar uma ideia exatamente na fronteira entre dois chunks.
OVERLAP_CHARS = 90
# Cauda residual menor que isto é descartada (evita chunk-lixo no fim da seção).
MIN_BLOCK_CHARS = 40


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


def _to_pieces(text: str, target: int) -> list[str]:
    """Quebra o texto em 'peças' indivisíveis <= target quando possível: por
    parágrafo; parágrafo grande vira linhas; linha gigante vira fatias fixas."""
    pieces: list[str] = []
    for para in re.split(r"\n\s*\n", text):
        para = para.strip("\n")
        if not para.strip():
            continue
        if len(para) <= target:
            pieces.append(para)
            continue
        for line in para.split("\n"):
            if len(line) <= target:
                if line.strip():
                    pieces.append(line)
            else:
                for k in range(0, len(line), target):
                    frag = line[k:k + target]
                    if frag.strip():
                        pieces.append(frag)
    return pieces


def _pack(pieces: list[str], target: int, overlap: int) -> list[str]:
    """Agrupa peças em blocos de ~target chars, repetindo a cauda (~overlap chars,
    a partir de uma fronteira de espaço) no início do bloco seguinte."""
    blocks: list[str] = []
    cur = ""
    for piece in pieces:
        if cur and len(cur) + len(piece) + 2 > target:
            blocks.append(cur)
            tail = cur[-overlap:] if overlap and len(cur) > overlap else ""
            if tail:
                sp = tail.find(" ")
                if sp != -1:
                    tail = tail[sp + 1:]
                cur = f"{tail}\n\n{piece}"
            else:
                cur = piece
        else:
            cur = f"{cur}\n\n{piece}" if cur else piece
    if cur:
        blocks.append(cur)
    return blocks


def _section_body(section_text: str) -> str:
    """Remove a 1ª linha (o heading `##`) e devolve o corpo da seção."""
    nl = section_text.find("\n")
    return section_text[nl + 1:].strip() if nl != -1 else ""


def _emit(filepath: Path, source: str, doc_title: str, section_heading: str,
          body: str, min_chars: int, target: int, overlap: int) -> list[dict]:
    """Gera os sub-chunks de uma seção (ou do documento inteiro, se sem `##`)."""
    body = body.strip()
    if len(body) < min_chars:
        return []
    prefix = f"{doc_title} › {section_heading}".strip(" ›") if section_heading else (doc_title or "")
    slug_base = slugify(section_heading) if section_heading else "documento"
    blocks = _pack(_to_pieces(body, target), target, overlap) or [body]

    out: list[dict] = []
    for j, block in enumerate(blocks):
        block = block.strip()
        if not block or (len(block) < MIN_BLOCK_CHARS and len(blocks) > 1):
            continue
        content = f"{prefix}\n\n{block}" if prefix else block
        out.append({
            "id": f"{filepath.stem}::{slug_base}::{j}",
            "source": source,
            "doc_title": doc_title,
            "section": section_heading or doc_title,
            "content": content,
            "chars": len(content),
        })
    return out


def split_into_chunks(filepath: Path, docs_dir: Path, min_chars: int,
                      target: int = TARGET_CHARS, overlap: int = OVERLAP_CHARS) -> list[dict]:
    content = filepath.read_text(encoding="utf-8")
    doc_title = extract_doc_title(content)
    source = filepath.relative_to(docs_dir.parent).as_posix()

    splits = list(H2_PATTERN.finditer(content))

    if not splits:
        # Documento sem `##`: trata o corpo inteiro (menos o título `#`) como uma seção.
        body = re.sub(r"^# .+\n?", "", content, count=1).strip()
        return _emit(filepath, source, doc_title, "", body, min_chars, target, overlap)

    chunks: list[dict] = []
    for i, match in enumerate(splits):
        start = match.start()
        end = splits[i + 1].start() if i + 1 < len(splits) else len(content)
        section_text = content[start:end].strip()
        section_heading = match.group(0).lstrip("# ").strip()
        body = _section_body(section_text)
        chunks.extend(_emit(filepath, source, doc_title, section_heading, body,
                            min_chars, target, overlap))

    return chunks


def chunk_docs(docs_dir: Path, min_chars: int,
               target: int = TARGET_CHARS, overlap: int = OVERLAP_CHARS) -> list[dict]:
    all_chunks: list[dict] = []
    for filepath in discover_md_files(docs_dir):
        all_chunks.extend(split_into_chunks(filepath, docs_dir, min_chars, target, overlap))
    return all_chunks


def main():
    parser = argparse.ArgumentParser(description="Gera chunks RAG a partir dos docs markdown de um projeto")
    parser.add_argument("--docs-dir", default="docs",
                        help="Pasta de docs, relativa à raiz do projeto ou absoluta (padrão: docs)")
    parser.add_argument("--out", help="Arquivo de saída JSON (padrão: stdout)")
    parser.add_argument("--min-chars", type=int, default=80, metavar="N",
                        help="Ignora seções com menos de N caracteres (padrão: 80)")
    parser.add_argument("--target-chars", type=int, default=TARGET_CHARS, metavar="N",
                        help=f"Tamanho-alvo do corpo de cada sub-chunk (padrão: {TARGET_CHARS})")
    parser.add_argument("--overlap-chars", type=int, default=OVERLAP_CHARS, metavar="N",
                        help=f"Sobreposição entre sub-chunks vizinhos (padrão: {OVERLAP_CHARS})")
    args = parser.parse_args()

    docs_dir = resolve_path(args.docs_dir)
    if not docs_dir.exists():
        sys.exit(f"Diretório de docs não encontrado: {docs_dir}")

    all_chunks = chunk_docs(docs_dir, args.min_chars, args.target_chars, args.overlap_chars)
    output = json.dumps(all_chunks, ensure_ascii=False, indent=2)

    if args.out:
        Path(args.out).write_text(output, encoding="utf-8")
        print(f"{len(all_chunks)} chunks → {args.out}", file=sys.stderr)
    else:
        print(output)


if __name__ == "__main__":
    main()
