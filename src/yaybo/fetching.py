"""One queue of work, and one worker moving through it.

Fetching used to belong to whichever screen started it, which meant a building
of thirty flats pinned you to that screen for the length of the run. The queue
lives on the application instead: screens put work into it and read its state,
the worker carries on across screen changes, and every screen shows the same
progress because they are all looking at the same object.

Nothing here imports Textual. The application owns the thread; this owns what
the thread is doing, which is what lets it be tested without a terminal.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass, field
from itertools import count

from yaybo import pipeline, store
from yaybo.register.address import AddressError

# HELD is queued but parked: added while auto-fetch was off, and picked up
# only when someone says to start it. WAITING is queued and will be taken by
# the worker as soon as it gets there.
HELD, WAITING, RUNNING, DONE, FAILED = "⏸", "·", "⟳", "✓", "✗"
# Between properties. The register is a public service, not a scraping target.
POLITE_DELAY = 1.0


@dataclass
class Job:
    """One thing to fetch: either resolved properties, or an address to resolve.

    Both shapes sit in the same list because they end the same way - rows in the
    database - and someone watching a progress bar does not care which kind of
    thing produced them.
    """

    label: str
    address: dict | None = None
    units: list[dict] = field(default_factory=list)
    query: str = ""
    # Stable for the life of the job, so a screen can remember which rows are
    # ticked across a redraw that reorders or removes some of them.
    key: str = ""
    # The properties this job wrote, once it knows them. Taken from the units
    # up front where there are any, and from the result where there are not.
    uuids: list[str] = field(default_factory=list)
    state: str = WAITING
    note: str = ""
    # What this job is expected to cost, and how much of that is spent. A guess
    # until the register has been asked: a raw address might be one house or a
    # hundred and eighteen flats, and only the register knows which.
    expected: int = 1
    fetched: int = 0
    rows: int = 0
    # Query jobs only: how many properties one address is allowed to cost.
    # Re-fetching one flat sets 1, because the address it was stored under
    # resolves to the whole building when the register has no separate entry
    # for that flat - and re-fetching one row must not fetch a hundred.
    limit: int = 0

    def __post_init__(self) -> None:
        if self.units:
            self.expected = len(self.units)
            self.uuids = [u["uuid"] for u in self.units if u.get("uuid")]

    @property
    def finished(self) -> bool:
        return self.state in (DONE, FAILED)

    @property
    def startable(self) -> bool:
        """Whether telling this job to start would mean anything."""
        return self.state in (HELD, FAILED)


class FetchQueue:
    """What is waiting, what is in flight, and what it has already written."""

    def __init__(self) -> None:
        self.jobs: list[Job] = []
        # The property being fetched right now, for whoever is showing a bar.
        self.current: str = ""
        self.running = False
        # Whether a job starts fetching the moment it is added. Off means
        # things pile up in the queue until someone starts them, which is what
        # you want when gathering a list of buildings to fetch in one go.
        self.auto = True
        self._numbers = count(1)
        self._stop = threading.Event()
        # Held for the length of a drain. Two screens can ask for the queue to
        # start at the same moment; the second must be a no-op rather than a
        # second worker racing the first through the same list.
        self._draining = threading.Lock()

    # ── what is in it ───────────────────────────────────────────────────

    def _admit(self, job: Job) -> Job:
        """Give a job its key and decide whether it starts life waiting."""
        job.key = str(next(self._numbers))
        job.state = WAITING if self.auto else HELD
        self.jobs.append(job)
        return job

    def add_units(self, label: str, address: dict, units: list[dict]) -> Job:
        """Queue properties the register has already been asked about."""
        return self._admit(Job(label=label, address=address, units=list(units)))

    def add_query(self, query: str, label: str = "", limit: int = 0) -> Job | None:
        """Queue a raw address, unless the same one is already unfinished."""
        if any(job.query == query and not job.finished for job in self.jobs):
            return None
        return self._admit(Job(label=label or query, query=query, limit=limit))

    def chosen(self, keys: set[str] | None) -> list[Job]:
        """The jobs a screen has ticked, or all of them if it has ticked none.

        Nothing ticked meaning everything is the rule throughout this queue: a
        list with no selection reads as "this list", and making people tick all
        forty rows to act on forty rows is not a safety feature.
        """
        if not keys:
            return list(self.jobs)
        return [job for job in self.jobs if job.key in keys]

    def uuids_for(self, keys: set[str] | None) -> list[str]:
        """Every property the chosen jobs have fetched, for exporting them."""
        found: list[str] = []
        for job in self.chosen(keys):
            found.extend(uuid for uuid in job.uuids if uuid not in found)
        return found

    @property
    def waiting(self) -> int:
        return sum(1 for job in self.jobs if job.state == WAITING)

    @property
    def held(self) -> int:
        return sum(1 for job in self.jobs if job.state == HELD)

    @property
    def failed(self) -> int:
        return sum(1 for job in self.jobs if job.state == FAILED)

    @property
    def total(self) -> int:
        return sum(job.expected for job in self.jobs)

    @property
    def done(self) -> int:
        return sum(job.fetched for job in self.jobs)

    @property
    def rows(self) -> int:
        return sum(job.rows for job in self.jobs)

    @property
    def active(self) -> bool:
        """Whether there is anything worth showing a progress bar for."""
        return self.running or bool(self.waiting)

    def start(self, keys: set[str] | None = None) -> int:
        """Let the chosen held or failed jobs be picked up. Returns how many."""
        started = [job for job in self.chosen(keys) if job.startable]
        for job in started:
            job.state, job.note, job.fetched = WAITING, "", 0
        return len(started)

    def retry(self, keys: set[str] | None = None) -> int:
        """Put the chosen failed jobs back in the queue."""
        retried = [job for job in self.chosen(keys) if job.state == FAILED]
        for job in retried:
            job.state, job.note, job.fetched = WAITING, "", 0
        return len(retried)

    def remove(self, keys: set[str] | None = None) -> int:
        """Drop the chosen jobs, except one in flight - that cannot be undone."""
        going = {job.key for job in self.chosen(keys) if job.state != RUNNING}
        self.jobs = [job for job in self.jobs if job.key not in going]
        return len(going)

    def clear_finished(self) -> None:
        self.jobs = [job for job in self.jobs if not job.finished]

    # ── working through it ──────────────────────────────────────────────

    def stop(self) -> None:
        """Ask the worker to stop after the property it is on."""
        self._stop.set()

    @property
    def stopping(self) -> bool:
        return self._stop.is_set()

    def run(self, api, database, changed) -> None:
        """Work through everything waiting. Blocking - call it on a thread.

        `changed()` is called whenever something a screen might be showing has
        moved. It runs on this thread, so getting back to the UI thread is the
        caller's problem, not this object's.
        """
        if not self._draining.acquire(blocking=False):
            return
        self.running = True
        self._stop.clear()
        changed()
        try:
            while not self._stop.is_set():
                # Re-read the list each time round rather than taking a copy:
                # the point of this queue is that more can be added to it while
                # it is running.
                job = next((j for j in self.jobs if j.state == WAITING), None)
                if job is None:
                    break
                job.state, job.note = RUNNING, ""
                changed()
                self._do(api, database, job, changed)
        finally:
            self.running = False
            self.current = ""
            self._stop.clear()
            self._draining.release()
            changed()

    def _do(self, api, database, job: Job, changed) -> None:
        def on_unit(index: int, total: int, unit: dict) -> None:
            # Called before each property, so index - 1 of them are really done.
            job.expected = max(total, 1)
            job.fetched = index - 1
            self.current = unit.get("adresse") or job.label
            changed()

        try:
            # Both halves of Job are optional on the dataclass, so the shape is
            # settled here rather than trusted: a units job always carries the
            # address its units were listed under.
            if job.units and job.address is not None:
                bundle = pipeline.fetch(
                    api,
                    job.address,
                    job.units,
                    delay=POLITE_DELAY,
                    on_unit=on_unit,
                    should_stop=self._stop.is_set,
                )
            else:
                bundle = pipeline.lookup(
                    api,
                    job.query,
                    limit=job.limit,
                    delay=POLITE_DELAY,
                    on_unit=on_unit,
                    should_stop=self._stop.is_set,
                )
            written = store.save(database, bundle.tables)
        except AddressError as error:
            job.state, job.note = FAILED, str(error)
            job.fetched = job.expected
            changed()
            return
        except Exception as error:  # noqa: BLE001 - shown in the job's own row
            job.state = FAILED
            job.note = f"{type(error).__name__}: {error}"
            # Whatever it never reached still has to come off the bar, or one
            # failure freezes the progress of everything queued behind it.
            job.fetched = job.expected
            changed()
            return

        job.rows = sum(written.values())
        attempted = max(job.fetched, len(bundle.properties))
        if self._stop.is_set() and attempted < job.expected:
            # Stopped partway. What was fetched is saved and stays saved; the
            # job goes back in the queue so starting again picks up the rest
            # rather than quietly dropping it.
            job.fetched = attempted
            job.state = WAITING
            job.note = "stopped partway - start again to finish it"
        else:
            # Everything it was asked for has been attempted, so the whole job
            # comes off the bar. Counting attempts rather than rows on purpose:
            # a property the register holds nothing for is still one this run
            # has dealt with, and the bar must not stall on it forever.
            job.expected = max(job.expected, attempted)
            job.fetched = job.expected
            job.state = DONE
            job.note = bundle.warning
            # A query job only learns which properties it fetched by fetching
            # them; a units job knew all along. Either way this is what makes
            # "export the ones I ticked" possible afterwards.
            for row in bundle.properties:
                if row.get("uuid") and row["uuid"] not in job.uuids:
                    job.uuids.append(row["uuid"])
        self.current = ""
        changed()
