"""CLI : python -m audio2k {hash,plan,fetch,extract} ..."""
import argparse
import asyncio
import logging
import os
import sys

from . import avi
from .ed2k import proto as P
from .fetch import Fetcher, load_identity
from .md4 import ed2k_hash
from .plan import Plan
from .store import Store


def cmd_hash(a):
    size = os.path.getsize(a.file)
    with open(a.file, "rb") as f:
        h, parts = ed2k_hash(f, size)
    print(P.ed2k_link(os.path.basename(a.file), size, bytes.fromhex(h)))
    for i, p in enumerate(parts):
        print(f"  part {i}: {p.hex().upper()}")


def cmd_plan(a):
    sys.argv = ["avi", a.file]
    import runpy
    runpy.run_module("audio2k.avi", run_name="__main__")


def _plan(a):
    name, size, fh = P.parse_ed2k_link(a.link)
    store = Store(a.state, fh, size)
    return name, size, fh, Plan(store, mode=a.want, gap=a.gap)


def cmd_fetch(a):
    name, size, fh, plan = _plan(a)
    ident = load_identity(a.state, a.port, a.nick)
    logging.info("fichier « %s » %d o, hash %s, identité %s@%d", name, size, fh.hex().upper(), ident.nick, ident.tcp_port)
    server = P.parse_ed2k_link(a.server) if a.server else None
    direct = [(d.rsplit(":", 1)[0], int(d.rsplit(":", 1)[1])) for d in a.direct]
    if not server and not direct:
        sys.exit("--server ou --direct requis")
    if a.seed:
        plan.verify_parts()
    f = Fetcher(plan, fh, size, ident, server=server, direct=direct, pipeline=a.pipeline,
                only_low=a.only_low, seed=a.seed, name=name)
    try:
        asyncio.run(f.run())
    except KeyboardInterrupt:
        pass
    plan.store.save()
    logging.info("état sauvegardé : %d o acquis", plan.store.have.total())
    if a.extract:
        cmd_extract(a)


def cmd_seed(a):
    """Importe des octets depuis une copie locale (banc d'essai, ou .part récupéré ailleurs)."""
    name, size, fh, plan = _plan(a)
    with open(a.source, "rb") as f:
        if a.range:
            ranges = [tuple(int(v) for v in a.range.split(":"))]
        elif a.all:
            ranges = [(0, size)]
        else:
            ranges = None                                # en-tête puis idx1, en trois passes
        if ranges is None:
            for _ in range(3):
                for x, y in plan.store.missing(plan.wanted()):
                    f.seek(x); plan.store.write(x, f.read(y - x))
        else:
            for x, y in plan.store.missing(ranges):
                f.seek(x); plan.store.write(x, f.read(y - x))
        if os.path.getsize(a.source) == size and plan.hashset is None:
            f.seek(0)
            h, parts = ed2k_hash(f, size)
            if bytes.fromhex(h) == fh:
                plan.set_hashset(parts)
    plan.store.save()
    print(f"phase {plan.phase()}, {plan.store.have.total()} o dans l'état, hashset {'ok' if plan.hashset else 'absent'}")


def cmd_probe(a):
    """Login serveur + liste des sources du fichier, sans rien télécharger."""
    from .ed2k.server import Server
    name, size, fh = P.parse_ed2k_link(a.link)
    ident = load_identity(a.state, a.port, a.nick)

    async def go():
        # le serveur teste notre port pendant le login (Hello à répondre) : on écoute comme en vrai
        f = Fetcher(_plan(a)[3], fh, size, ident)
        listener = await f.listen()
        s = Server(*P.parse_ed2k_link(a.server), ident)
        cid = await s.connect()
        listener.close()
        print("ID :", cid, "(LOW ID : port non joignable)" if P.is_low_id(cid) else f"(HIGH ID {P.ip_str(cid)})")
        srcs = await s.sources(fh, size)
        for c, p in srcs:
            print("source :", f"low ID {c}" if P.is_low_id(c) else f"{P.ip_str(c)}:{p}")
        if not srcs:
            print("aucune source pour ce fichier en ce moment")
        s.close()
    asyncio.run(go())


def cmd_export_part(a):
    """Écrit <n>.part et <n>.part.met (gaps = octets manquants) pour import dans eMule/aMule."""
    from pathlib import Path
    from .partmet import export
    name, size, fh, plan = _plan(a)
    ok, bad = plan.verify_parts()
    part, met = export(plan.store, plan, name, Path(a.out_dir), a.number)
    print(f"{part} + {met} : {plan.store.have.total()} o présents, parts complètes vérifiées {sorted(plan.verified)}"
          + (f", CORROMPUES {bad}" if bad else ""))


def cmd_extract(a):
    name, size, fh, plan = _plan(a)
    out = a.extract if getattr(a, "extract", None) else a.out
    ok, miss = plan.extract_mp3(out)
    print(f"{out} : {ok} chunks audio écrits, {miss} manquants")


def main():
    ap = argparse.ArgumentParser(prog="audio2k")
    ap.add_argument("-v", action="store_true", help="logs debug")
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("hash", help="lien ed2k d'un fichier local"); s.add_argument("file"); s.set_defaults(fn=cmd_hash)
    s = sub.add_parser("plan", help="analyse locale d'un AVI : plages audio à demander"); s.add_argument("file"); s.set_defaults(fn=cmd_plan)
    for nm, fn in (("fetch", cmd_fetch), ("extract", cmd_extract), ("seed", cmd_seed), ("probe", cmd_probe),
                   ("export-part", cmd_export_part)):
        s = sub.add_parser(nm)
        s.add_argument("--link", required=True, help="lien ed2k://|file|...")
        s.add_argument("--state", default="state", help="dossier d'état (fichier épars, carte, identités)")
        s.add_argument("--want", choices=["audio", "all"], default="audio", help="audio seul (obj. 1) ou parts entières (obj. 2)")
        s.add_argument("--gap", type=int, default=avi.SENDINGPART_OVERHEAD, help="fusionner des plages séparées de moins de N octets")
        s.set_defaults(fn=fn)
    for nm in ("fetch", "probe"):
        s = sub.choices[nm]
        s.add_argument("--server", help="lien ed2k://|server|ip|port|/")
        s.add_argument("--port", type=int, default=4662, help="port TCP redirigé (high ID) sur lequel écouter")
        s.add_argument("--nick", default="audio2k")
    s = sub.choices["fetch"]
    s.add_argument("--direct", action="append", default=[], help="host:port d'un uploader HIGH ID (banc d'essai), répétable, sans serveur")
    s.add_argument("--only-low", action="store_true", help="ignorer les sources HIGH ID (ne viser que la source low ID)")
    s.add_argument("--seed", action="store_true",
                   help="repartager : servir aux autres clients les parts complètes vérifiées, et s'annoncer au serveur")
    s.add_argument("--pipeline", type=int, default=64 * 1024, help="octets demandés en avance par slot")
    s.add_argument("--extract", help="à la fin, écrire le MP3 ici")
    sub.choices["extract"].add_argument("--out", default="audio.mp3")
    sub.choices["export-part"].add_argument("--out-dir", default=".", help="dossier Temp d'eMule/aMule (ou autre)")
    sub.choices["export-part"].add_argument("--number", type=int, default=900, help="numéro du .part (éviter ceux déjà pris)")
    sub.choices["seed"].add_argument("--source", required=True, help="copie locale du fichier")
    sub.choices["seed"].add_argument("--all", action="store_true", help="tout importer, pas seulement en-tête et idx1")
    sub.choices["seed"].add_argument("--range", help="n'importer que les octets a:b (b exclusif)")
    a = ap.parse_args()
    logging.basicConfig(level=logging.DEBUG if a.v else logging.INFO, format="%(asctime)s %(name)-6s %(message)s", datefmt="%H:%M:%S")
    a.fn(a)


if __name__ == "__main__":
    main()
