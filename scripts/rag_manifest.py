"""
RAG Manifest — o "mapa de indexação" versionado (rag.lock.json).

Em vez de versionar o índice binário do Chroma (chroma_db/, ~8 MB de .bin do HNSW
que incham o repositório e dão conflito de merge), versionamos um MANIFESTO de
texto (~poucos KB) que descreve, de forma determinística, tudo o que é preciso
para reconstruir a MESMA indexação em qualquer máquina:

  - o modelo de embedding (nome, dimensão, espaço de distância);
  - os parâmetros de chunking + a versão do esquema do chunker;
  - a lista de docs indexados, cada um com seu sha256 e a contagem de chunks;
  - a toolchain instalada (chromadb / sentence-transformers / transformers / torch).

O índice do Chroma é uma FUNÇÃO PURA dessas entradas (docs + chunker + modelo) —
logo não precisa ser versionado: qualquer dev roda `rag_ingest.py` e obtém a mesma
indexação. Este manifesto trava essa função e permite `rag_verify.py` detectar
drift (doc alterado, modelo trocado, esquema mudado) sem comparar 1 byte binário.

Camadas de comparação (ver `diff_manifest`):
  - HARD  (estrutural, plataforma-independente): schema, modelo, params de chunk,
          coleção e os hashes/contagens dos docs. Divergência aqui = índice fora
          de sync → re-ingerir.
  - SOFT  (toolchain): versões de libs. Divergência gera apenas AVISO — exigir
          match exato quebraria cross-plataforma (ex.: `torch==2.x+cpu` só existe
          no Linux CPU). O que garante os vetores é usar o MESMO modelo; variações
          mínimas de float entre versões de lib não mudam o ranking top-k.

Parte do fluxo de documentação do projeto (ver CLAUDE.md → Documentação e
docs/RAG-INDEXACAO.md). Importado por rag_ingest.py e rag_verify.py.
"""

import hashlib
import json
import sys
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from rag_chunker import (  # noqa: E402
    MIN_BLOCK_CHARS,
    OVERLAP_CHARS,
    ROOT_DIR,
    SCHEMA_VERSION,
    TARGET_CHARS,
    discover_md_files,
    resolve_path,
    slugify,
    split_into_chunks,
)
from rag_embedding import EMBED_DIMS, EMBED_MODEL, EMBED_SPACE  # noqa: E402

# Onde o manifesto versionado vive (fica junto do pipeline, na pasta scripts/).
LOCK_PATH = ROOT_DIR / "scripts" / "rag.lock.json"

# Mesmo default do rag_ingest.py: filtro de seções curtas.
DEFAULT_MIN_CHARS = 80

# Pacotes registrados na toolchain (comparação SOFT). torch/transformers são
# transitivos do sentence-transformers, mas entram no registro porque é onde o
# modelo de fato roda — útil para diagnosticar drift de vetores.
_TOOLCHAIN_PACKAGES = ("chromadb", "sentence-transformers", "transformers", "torch")


def default_collection_name() -> str:
    """Mesma convenção de rag_ingest.py/rag_search.py: <pasta-do-projeto>_docs."""
    return f"{slugify(ROOT_DIR.name)}_docs"


def _toolchain() -> dict:
    out: dict = {}
    for pkg in _TOOLCHAIN_PACKAGES:
        try:
            out[pkg] = version(pkg)
        except PackageNotFoundError:
            out[pkg] = None
    return out


def compute_manifest(
    docs_dir: Path | None = None,
    collection: str | None = None,
    min_chars: int = DEFAULT_MIN_CHARS,
    target: int = TARGET_CHARS,
    overlap: int = OVERLAP_CHARS,
) -> dict:
    """Computa o manifesto do estado ATUAL dos docs + config, sem carregar o
    modelo de embedding (rápido: só lê arquivos e roda o chunker). Determinístico
    — sem timestamp — para dar diff limpo no git (só muda quando docs/config mudam)."""
    docs_dir = docs_dir or resolve_path("docs")
    if not docs_dir.exists():
        raise FileNotFoundError(f"Diretório de docs não encontrado: {docs_dir}")

    docs: list[dict] = []
    total = 0
    for filepath in discover_md_files(docs_dir):
        # Hash sobre o TEXTO normalizado (read_text usa universal newlines:
        # CRLF/CR -> LF), não sobre os bytes crus. Sem isso, o sha256 dependeria do
        # line-ending do checkout de cada dev (git autocrlf no Windows daria hash
        # diferente do Linux para o MESMO conteúdo, gerando falso "doc alterado" no
        # verify). Alinha o hash ao que o chunker realmente enxerga — ele também lê
        # via read_text, por isso a contagem de chunks já é estável entre plataformas.
        text = filepath.read_text(encoding="utf-8")
        n = len(split_into_chunks(filepath, docs_dir, min_chars, target, overlap))
        total += n
        docs.append(
            {
                "source": filepath.relative_to(docs_dir.parent).as_posix(),
                "sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(),
                "chunks": n,
            }
        )
    docs.sort(key=lambda d: d["source"])

    return {
        "schema_version": SCHEMA_VERSION,
        "collection": collection or default_collection_name(),
        "embedding": {"model": EMBED_MODEL, "dims": EMBED_DIMS, "space": EMBED_SPACE},
        "chunking": {
            "target_chars": target,
            "overlap_chars": overlap,
            "min_block_chars": MIN_BLOCK_CHARS,
            "min_chars": min_chars,
        },
        "docs": docs,
        "total_chunks": total,
        "toolchain": _toolchain(),
    }


def write_lock(manifest: dict, path: Path = LOCK_PATH) -> Path:
    """Grava o manifesto como JSON estável (chaves ordenadas, UTF-8, newline final)
    para minimizar ruído de diff entre gerações."""
    text = json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True)
    path.write_text(text + "\n", encoding="utf-8")
    return path


def load_lock(path: Path = LOCK_PATH) -> dict | None:
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def diff_manifest(current: dict, locked: dict) -> dict:
    """Compara o manifesto atual contra o do lock. Retorna
    {"hard": [msgs], "soft": [msgs]}: 'hard' = fora de sync (re-ingerir);
    'soft' = apenas aviso de toolchain."""
    hard: list[str] = []
    soft: list[str] = []

    if current.get("schema_version") != locked.get("schema_version"):
        hard.append(
            f"esquema de chunking mudou (lock={locked.get('schema_version')} "
            f"→ atual={current.get('schema_version')})"
        )
    if current.get("collection") != locked.get("collection"):
        hard.append(f"coleção mudou (lock={locked.get('collection')} → atual={current.get('collection')})")

    for key in ("model", "dims", "space"):
        cv = current.get("embedding", {}).get(key)
        lv = locked.get("embedding", {}).get(key)
        if cv != lv:
            hard.append(f"embedding.{key} mudou (lock={lv} → atual={cv})")

    for key in ("target_chars", "overlap_chars", "min_block_chars", "min_chars"):
        cv = current.get("chunking", {}).get(key)
        lv = locked.get("chunking", {}).get(key)
        if cv != lv:
            hard.append(f"chunking.{key} mudou (lock={lv} → atual={cv})")

    cur_docs = {d["source"]: d for d in current.get("docs", [])}
    lock_docs = {d["source"]: d for d in locked.get("docs", [])}
    for source in sorted(set(cur_docs) - set(lock_docs)):
        hard.append(f"doc novo, ainda não indexado: {source}")
    for source in sorted(set(lock_docs) - set(cur_docs)):
        hard.append(f"doc removido, ainda no índice: {source}")
    for source in sorted(set(cur_docs) & set(lock_docs)):
        c, lk = cur_docs[source], lock_docs[source]
        if c["sha256"] != lk["sha256"]:
            hard.append(f"doc alterado (conteúdo mudou): {source}")
        elif c["chunks"] != lk["chunks"]:
            hard.append(f"doc com contagem de chunks diferente: {source} (lock={lk['chunks']} → atual={c['chunks']})")

    if current.get("total_chunks") != locked.get("total_chunks"):
        hard.append(
            f"total de chunks mudou (lock={locked.get('total_chunks')} → atual={current.get('total_chunks')})"
        )

    cur_tc = current.get("toolchain", {})
    lock_tc = locked.get("toolchain", {})
    for pkg in sorted(set(cur_tc) | set(lock_tc)):
        cv, lv = cur_tc.get(pkg), lock_tc.get(pkg)
        if cv != lv:
            soft.append(f"{pkg}: lock={lv} → instalado={cv}")

    return {"hard": hard, "soft": soft}
