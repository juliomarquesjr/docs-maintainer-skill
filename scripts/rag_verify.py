"""
RAG Verify — confere se a indexação LOCAL está em sync com o rag.lock.json.

Não versionamos o índice binário do Chroma; versionamos o "mapa" (rag.lock.json,
ver scripts/rag_manifest.py). Este comando é o que fecha o ciclo: depois de um
`git pull`, o dev roda `rag_verify.py` e sabe se precisa reconstruir o índice.

Duas checagens:
  1) LOCK vs DOCS ATUAIS — os docs/*.md em disco batem com o que o lock registrou?
     (hashes, contagem de chunks, modelo, params, esquema). Divergência HARD aqui
     significa que a doc mudou mas o lock não foi regenerado — quem editou precisa
     rodar `rag_ingest.py` e commitar o rag.lock.json junto.
  2) LOCK vs CHROMA LOCAL — o chroma_db/ desta máquina tem a mesma contagem de
     chunks que o lock manda? É o caso típico pós-pull: lock veio novo, índice
     local está velho → rode `rag_ingest.py` para reconstruir (pule com --no-chroma).

Saída: exit 0 = em sync (avisos de toolchain não reprovam); exit 1 = fora de sync
(precisa re-ingerir); exit 2 = lock ausente. Use --json para consumo por agentes.

Uso:
    python rag_verify.py
    python rag_verify.py --no-chroma      # só valida lock vs docs, ignora o índice físico
    python rag_verify.py --json
"""

import argparse
import json
import sys
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

sys.path.insert(0, str(Path(__file__).resolve().parent))
from rag_chunker import resolve_path
from rag_manifest import LOCK_PATH, compute_manifest, diff_manifest, load_lock


def _chroma_count(chroma_path: Path, collection: str) -> int | None:
    """Contagem de chunks no índice local, ou None se não der para abrir a coleção
    (índice ainda não construído nesta máquina)."""
    try:
        import chromadb

        client = chromadb.PersistentClient(path=str(chroma_path))
        return client.get_collection(collection).count()
    except Exception:
        return None


def main() -> None:
    parser = argparse.ArgumentParser(description="Verifica se a indexação RAG local está em sync com o rag.lock.json")
    parser.add_argument("--docs-dir", default="docs", help="Pasta de docs (padrão: docs)")
    parser.add_argument("--chroma-path", default="chroma_db", help="Pasta do Chroma persistente (padrão: chroma_db)")
    parser.add_argument("--no-chroma", action="store_true", help="Não confere o índice físico local, só lock vs docs")
    parser.add_argument("--json", action="store_true", help="Saída estruturada (para agentes)")
    args = parser.parse_args()

    locked = load_lock()
    if locked is None:
        msg = f"rag.lock.json não encontrado ({LOCK_PATH}). Rode: rag_ingest.py"
        print(json.dumps({"status": "no-lock", "message": msg}) if args.json else f"✗ {msg}", file=sys.stderr)
        sys.exit(2)

    docs_dir = resolve_path(args.docs_dir)
    current = compute_manifest(docs_dir, collection=locked.get("collection"), min_chars=locked.get("chunking", {}).get("min_chars", 80))
    d = diff_manifest(current, locked)
    hard, soft = d["hard"], d["soft"]

    # Checagem do índice físico local (lock vs chroma_db desta máquina).
    chroma_note = None
    if not args.no_chroma:
        chroma_path = resolve_path(args.chroma_path)
        count = _chroma_count(chroma_path, locked.get("collection", ""))
        expected = locked.get("total_chunks")
        if count is None:
            hard.append(f"índice local ausente/inacessível em {chroma_path} — rode rag_ingest.py")
        elif count != expected:
            hard.append(f"índice local desatualizado: {count} chunks no Chroma vs {expected} no lock — rode rag_ingest.py")
        else:
            chroma_note = f"{count} chunks (bate com o lock)"

    status = "out-of-sync" if hard else "ok"
    if args.json:
        print(json.dumps({"status": status, "hard": hard, "soft": soft, "chroma": chroma_note}, ensure_ascii=False, indent=2))
    else:
        if hard:
            print("✗ Indexação FORA DE SYNC com o rag.lock.json:")
            for m in hard:
                print(f"  - {m}")
            print("\n  → Corrija rodando: .venv-docs/bin/python scripts/rag_ingest.py")
        else:
            print("✓ Indexação em sync com o rag.lock.json.")
            if chroma_note:
                print(f"  índice local: {chroma_note}")
        if soft:
            print("\n⚠ Toolchain difere do lock (apenas aviso — o modelo é o mesmo, o ranking não muda):")
            for m in soft:
                print(f"  - {m}")

    sys.exit(1 if hard else 0)


if __name__ == "__main__":
    main()
