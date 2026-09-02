"""yaybo on the command line - the same lookups the TUI does, with no screen.

    yaybo                                     the TUI
    yaybo fetch "Prøvegade 1, 9999 Prøveby"
    yaybo login --user YourMitIDUserID
    yaybo status
    yaybo backfill

`fetch` is what a cron job or a shell pipeline wants: one address in, a set of
tables out, and nothing to press. Everything it does, the TUI does too - they
share yaybo.pipeline, so neither can drift from the other.

Results accumulate in out/tinglysning.duckdb, replaced in place when an address
is looked up again. Pass --format csv or --format xlsx for files instead, or a
comma-separated list for several at once.

    ejendomme               one row per property, with what it is worth, what
                            is charged against it and what is left over
    ejere                   its owners today
    haeftelser              mortgages and charges, with their interest terms
                            and an estimate of which loan product they are
    servitutter             easements, and what each one is about
    dokument_parter         everyone named on any of those documents, with
                            their date of birth or CVR number and their role
    underpant               deeds pledged on in their own right
    handelshistorik         every recorded sale, with the price per m2
    bygninger               the BBR record: year built, rooms, heating, walls
    adkomsthistorik         every past transfer, with what was paid
    adkomsthistorik_ejere   the people named in each of those transfers
    attester                the whole register document, as queryable JSON

Three of those need a login - the register only shows its own documents to
someone who has proved who they are. Two more come from outside it entirely,
and need none: Boligsiden for sale prices and the BBR record, and Danmarks
Statistik for the rates that let a charge be read as an F3 or a fixed loan.
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
import time
from pathlib import Path

import mitid
from mitid.ui.console import LoginConsole

from yaybo import auth, export, pipeline, store
from yaybo.register.address import AddressError, drop_unit, slugify
from yaybo.register.client import Tinglysning

FORMATS = ("duckdb", "csv", "xlsx")


# ── the session ─────────────────────────────────────────────────────────


def start_login(user_id: str | None, method: str, password: str | None):
    """Run the MitID login on the terminal and return the live session."""
    screen = LoginConsole()
    if not user_id:
        # The user ID is the only thing we cannot guess, and typing it once per
        # machine is enough - the session file remembers it afterwards.
        remembered = auth.restore_session()
        user_id = (remembered[1].get("user_id") if remembered else "") or screen.ask(
            "MitID user ID:"
        )

    session, who = auth.log_in(
        user_id,
        method=method,
        password=password,
        on_status=screen.status,
        on_qr=screen.qr,
        on_otp=screen.otp,
        ask_token_code=screen.ask,
        choose_identity=screen.choose,
    )
    print(f"\n  logged in as {who}", file=sys.stderr)
    print(f"  session cached in {auth.session_path()}\n", file=sys.stderr)
    return session


def resume_login():
    """Pick a cached session back up, or return None if there is none to use."""
    remembered = auth.restore_session()
    if remembered is None:
        return None

    session, saved = remembered
    who = auth.who_is_logged_in(session)
    if who is None:
        print("cached session has expired - run `yaybo login` for the extra columns",
              file=sys.stderr)
        return None

    idle = auth.idle_for(saved.get("saved_at"))
    since = f", idle {int(idle.total_seconds() // 60)} min" if idle else ""
    print(f"authenticated as {who}{since}", file=sys.stderr)
    # Rewrite the file so its timestamp tracks last use, not the login.
    auth.save_session(session, saved.get("user_id", ""))
    return session


def show_status() -> int:
    """Say whether there is a usable session, and how much of it is left."""
    remembered = auth.restore_session()
    if remembered is None:
        print("not logged in - run `yaybo login` to start a session", file=sys.stderr)
        return 1

    session, saved = remembered
    who = auth.who_is_logged_in(session)
    idle = auth.idle_for(saved.get("saved_at"))
    if who is None:
        print(f"session for {saved.get('user_id') or 'unknown user'} has expired",
              file=sys.stderr)
        return 1

    print(f"logged in as {who} ({saved.get('user_id', '')})", file=sys.stderr)
    if idle:
        left = auth.IDLE_LIMIT - idle
        minutes = int(left.total_seconds() // 60)
        print(
            f"  last used {int(idle.total_seconds() // 60)} min ago"
            f" - about {max(minutes, 0)} min before it lapses",
            file=sys.stderr,
        )
    print(f"  cookies in {auth.session_path()}", file=sys.stderr)
    return 0


def hold_session(session, minutes: int, user_id: str = "") -> None:
    """Keep a session alive without logging in again.

    The register ends a session that has gone quiet, so this does what the
    site's own page does while someone has it open: says "still here", now and
    then, for as long as asked. Cheaper than another trip to the phone.
    """
    ping_every = 10 * 60  # comfortably inside the register's idle limit
    deadline = time.monotonic() + minutes * 60
    print(f"holding the session open for {minutes} min - Ctrl-C to stop", file=sys.stderr)
    try:
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            time.sleep(min(ping_every, remaining))
            if not auth.keep_alive(session):
                print("  the register ended the session anyway", file=sys.stderr)
                return
            auth.save_session(session, user_id)
            left = int((deadline - time.monotonic()) / 60)
            print(f"  still logged in, {max(left, 0)} min to go", file=sys.stderr)
    except KeyboardInterrupt:
        print("\n  stopped holding; the session stays valid for a while yet",
              file=sys.stderr)
        return
    print("  done holding - the session lapses shortly unless used", file=sys.stderr)


def _session(args):
    """The session a subcommand should run with, honouring --anonymous."""
    try:
        if getattr(args, "login", False):
            return start_login(args.user, args.method, args.password)
        if not getattr(args, "anonymous", False):
            return resume_login()
    except auth.AuthError as error:
        raise SystemExit(f"login failed: {error}") from error
    except KeyboardInterrupt:
        raise SystemExit("\nlogin cancelled") from None
    return None


# ── fetching ────────────────────────────────────────────────────────────


def run_fetch(args) -> int:
    formats = _formats(args.format)
    session = _session(args)
    api = Tinglysning(session)

    def say(message: str) -> None:
        print(message, file=sys.stderr)

    def each(index: int, total: int, unit: dict) -> None:
        print(f"  [{index}/{total}] {unit.get('adresse', '')}", file=sys.stderr)

    def raw(index, record, details, history) -> None:
        if args.dump and index == 1:
            _dump(args.dump, record, details, history)
            say(f"dumped the first raw record to {args.dump}")

    try:
        bundle = pipeline.lookup(
            api,
            args.address,
            limit=args.limit,
            use_dawa=not args.no_dawa,
            delay=args.delay,
            boligsiden_on=not args.no_boligsiden,
            laantype_on=not args.no_laantype,
            on_status=say,
            on_unit=each,
            on_raw=raw,
            on_session_expired=lambda done, total: _keep_going(done, total),
        )
    except AddressError as error:
        raise SystemExit(str(error)) from error
    if bundle.warning:
        say(f"note: {bundle.warning}")

    # Name the output after what was actually fetched. If the requested flat
    # had no separate entry we fell back to the whole building, so the flat
    # must not appear in the filename.
    label = bundle.address["tekst"]
    if bundle.warning:
        label = drop_unit(label)
    stem = Path(args.out).stem if args.out else slugify(label)
    outdir = Path(args.out).parent if args.out else Path(args.outdir)

    if "duckdb" in formats:
        database = Path(args.db) if args.db else store.default_path(args.outdir)
        written = store.save(database, bundle.tables)
        counts = ", ".join(f"{count} {name}" for name, count in written.items() if count)
        say(f"wrote {counts} to {database}")

    # The whole document belongs in a file of its own, not in a spreadsheet
    # cell: the register signs it, and a column holding 200 kB of XML makes
    # every other column unreadable. In the database it stays a column.
    documents = bundle.tables.get("attester") or []
    flat = {name: rows for name, rows in bundle.tables.items() if name != "attester"}

    for kind in formats:
        if kind == "duckdb":
            continue
        written = export.FORMATS[_label(kind)](flat, stem, outdir=outdir)
        paths = written if isinstance(written, list) else [written] if written else []
        for path in paths:
            say(f"wrote {path}")

    if documents and formats & {"csv", "xlsx"}:
        folder = outdir / f"{stem}-attester-{export.stamp()}"
        folder.mkdir(parents=True, exist_ok=True)
        for document in documents:
            name = f"{slugify(document['adresse'])}.{document['format']}"
            (folder / name).write_text(document["dokument"], encoding="utf-8")
        say(f"wrote {len(documents)} attest(er) to {folder}/")

    if args.keepalive and session is not None:
        hold_session(session, args.keepalive, args.user or "")
    return 0


def _keep_going(done: int, total: int) -> bool:
    """Decide what happens when the login lapses partway through a run.

    Carrying on keeps the run's work - every property still gets its public
    data, with the logged-in columns filled in for the first few. Stopping
    would keep the output honest about being one thing throughout. The first
    is the better trade for a command that may have been running for minutes.
    """
    print(f"  session expired after {done} of {total}", file=sys.stderr)
    return True


def _formats(spec: str) -> set[str]:
    """Read --format, which takes a comma-separated list."""
    if spec == "both":  # what it was called when there were only two
        return {"duckdb", "csv"}
    asked = {part.strip().lower() for part in spec.split(",") if part.strip()}
    unknown = asked - set(FORMATS)
    if unknown:
        raise SystemExit(f"unknown format(s): {', '.join(sorted(unknown))}")
    return asked or {"duckdb"}


def _label(kind: str) -> str:
    return {"csv": "CSV", "xlsx": "Excel (XLSX)", "duckdb": "DuckDB"}[kind]


# ── the parser ──────────────────────────────────────────────────────────


def _add_login_options(parser: argparse.ArgumentParser) -> None:
    group = parser.add_argument_group(
        "MitID login",
        "Logging in adds owners' dates of birth and previous owners. The "
        "session is remembered, so this is a once-in-a-while thing.",
    )
    group.add_argument("--login", action="store_true", help="log in with MitID first")
    group.add_argument("--user", help="your MitID user ID (not your CPR number)")
    group.add_argument(
        "--method",
        choices=[mitid.APP, mitid.TOKEN],
        default=mitid.APP,
        help="approve in the MitID app (default) or with a code token",
    )
    group.add_argument(
        "--password", help="MitID password, needed only with --method TOKEN"
    )
    group.add_argument(
        "--anonymous",
        action="store_true",
        help="ignore any cached session and use only the public lookup",
    )


def build_parser() -> argparse.ArgumentParser:
    # Both of these read the same either side of the subcommand, which is how
    # anyone actually types them. SUPPRESS is what makes that work: without it
    # the subparser would re-apply its own default and quietly wipe a value the
    # top-level parser had already taken.
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument(
        "--debug",
        action="store_true",
        default=argparse.SUPPRESS,
        help="show the protocol chatter, for when a login stops working",
    )
    common.add_argument(
        "--db",
        default=argparse.SUPPRESS,
        help=f"database path (default: {store.OUTDIR}/{store.DATABASE})",
    )

    parser = argparse.ArgumentParser(
        prog="yaybo", description=(__doc__ or "").splitlines()[0],
        parents=[common],
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    # No set_defaults here: parents=[] shares the action objects, and
    # set_defaults would reach in and overwrite SUPPRESS on the shared one -
    # which is exactly the wiping this was meant to avoid. main() fills the
    # gaps instead.
    commands = parser.add_subparsers(dest="command")

    fetch = commands.add_parser(
        "fetch", parents=[common], help="look an address up and write the tables"
    )
    fetch.add_argument("address", help='e.g. "Prøvegade 1, 9999 Prøveby"')
    fetch.add_argument(
        "--out", help="explicit output path (default: named after the address)"
    )
    fetch.add_argument(
        "--outdir",
        default=store.OUTDIR,
        help=f"where results go (default: {store.OUTDIR}/, which is git-ignored)",
    )
    fetch.add_argument(
        "--format",
        default="duckdb",
        metavar="LIST",
        help="comma-separated: duckdb (default), csv, xlsx",
    )
    fetch.add_argument(
        "--limit", type=int, default=25, help="max properties to fetch (0 = no limit)"
    )
    fetch.add_argument("--delay", type=float, default=1.0, help="seconds between fetches")
    fetch.add_argument(
        "--dump", metavar="PATH", help="write the first raw record as JSON"
    )
    fetch.add_argument(
        "--no-dawa",
        action="store_true",
        help="skip DAWA address cleaning and use tinglysning's own autocomplete",
    )
    fetch.add_argument(
        "--no-boligsiden",
        action="store_true",
        help="skip Boligsiden: no sale prices, no BBR building data, no equity",
    )
    fetch.add_argument(
        "--no-laantype",
        action="store_true",
        help="skip estimating each realkredit charge's loan type from DST rates",
    )
    fetch.add_argument(
        "--keepalive",
        nargs="?",
        type=int,
        const=60,
        metavar="MINUTES",
        help="hold the session open afterwards (default 60 minutes)",
    )
    _add_login_options(fetch)

    login = commands.add_parser(
        "login", parents=[common], help="log in with MitID and remember the session"
    )
    login.add_argument("--user", help="your MitID user ID (not your CPR number)")
    login.add_argument(
        "--method", choices=[mitid.APP, mitid.TOKEN], default=mitid.APP,
        help="approve in the MitID app (default) or with a code token",
    )
    login.add_argument(
        "--password", help="MitID password, needed only with --method TOKEN"
    )

    commands.add_parser(
        "logout", parents=[common], help="end the session and forget the cookies"
    )
    commands.add_parser(
        "status", parents=[common], help="say whether the session is still good"
    )

    hold = commands.add_parser(
        "keepalive", parents=[common], help="hold an existing session open"
    )
    hold.add_argument("minutes", nargs="?", type=int, default=60)

    backfill = commands.add_parser(
        "backfill",
        parents=[common],
        help="re-derive the stored tables without fetching anything",
    )
    backfill.add_argument("--dry-run", action="store_true",
                          help="report what would be written and change nothing")
    backfill.add_argument("--skip-boligsiden", action="store_true",
                          help="do not ask Boligsiden for sale prices and BBR data")
    backfill.add_argument("--skip-laantype", action="store_true",
                          help="do not estimate loan types from DST rates")
    backfill.add_argument("--delay", type=float, default=0.2, metavar="SECONDS",
                          help="pause between Boligsiden requests (default: 0.2)")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    # Absent rather than None when neither parser saw the flag; see above.
    args.debug = getattr(args, "debug", False)
    args.db = getattr(args, "db", None)

    # The vendored MitID client narrates itself through the logging module and
    # says the same things the console already shows, so it stays quiet unless
    # something has gone wrong enough to want the detail.
    logging.basicConfig(
        level=logging.DEBUG if args.debug else logging.CRITICAL,
        format="%(name)s: %(message)s",
        stream=sys.stderr,
    )

    if args.command == "fetch":
        return run_fetch(args)

    if args.command == "login":
        try:
            start_login(args.user, args.method, args.password)
        except auth.AuthError as error:
            raise SystemExit(f"login failed: {error}") from error
        except KeyboardInterrupt:
            raise SystemExit("\nlogin cancelled") from None
        return 0

    if args.command == "logout":
        remembered = auth.restore_session()
        if remembered:
            auth.log_out(remembered[0])
        print("logged out" if remembered else "no session to log out of", file=sys.stderr)
        return 0

    if args.command == "status":
        return show_status()

    if args.command == "keepalive":
        session = resume_login()
        if session is None:
            raise SystemExit("nothing to hold open - log in first")
        hold_session(session, args.minutes)
        return 0

    if args.command == "backfill":
        from yaybo import backfill

        return backfill.run(args)

    # No subcommand: the TUI, which is what most of this is for.
    from yaybo.app import run

    return run(database=args.db)


def _dump(path: str, record: dict, details, history) -> None:
    """Write the raw payloads for one property, side by side.

    The public record's shape is known; the two logged-in ones are not, so this
    is how their fields get named properly rather than guessed at.
    """
    payload = {"public": record}
    if details is not None:
        payload["ejdsummarisk"] = details
    if history is not None:
        payload["ejdhistoriskadkomst"] = history
    Path(path).write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )


if __name__ == "__main__":
    raise SystemExit(main())
