"""What the fetch queue does with a list of work, without a terminal.

The queue deliberately imports no Textual: the application owns the thread and
this owns what the thread is doing. That split is only worth having if it is
used, so everything here drives `FetchQueue.run` directly and checks what it
did to the jobs - the states, the accounting behind the progress bar, and what
survives being stopped halfway.

Nothing here touches the network. `pipeline.fetch` and `pipeline.lookup` are
replaced, because what is being tested is the queue's bookkeeping rather than
theirs.
"""

from __future__ import annotations

import pytest

from yaybo import fetching, pipeline, store
from yaybo.fetching import DONE, FAILED, HELD, WAITING, FetchQueue
from yaybo.pipeline import Bundle
from yaybo.register.address import AddressError

ADDRESS = {
    "tekst": "Prøvegade 1, 9999 Prøveby",
    "vejnavn": "Prøvegade",
    "husnummer": "1",
    "postnummer": "9999",
    "etage": "",
    "doer": "",
}


def units(count: int) -> list[dict]:
    return [
        {"uuid": f"u{i}", "adresse": f"Prøvegade 1, {i}. tv, 9999 Prøveby"}
        for i in range(count)
    ]


def rows(uuids: list[str]) -> dict[str, list[dict]]:
    return {"ejendomme": [{"uuid": uuid, "adresse": "Prøvegade 1"} for uuid in uuids]}


@pytest.fixture
def drained(monkeypatch, tmp_path):
    """Run a queue to completion against stand-ins, and report what happened."""
    seen: dict = {"units": [], "queries": []}

    def fake_fetch(api, address, given, **options):
        on_unit = options.get("on_unit")
        stop = options.get("should_stop")
        done = []
        for index, unit in enumerate(given, start=1):
            if stop and stop():
                break
            if on_unit:
                on_unit(index, len(given), unit)
            done.append(unit["uuid"])
        seen["units"].append(list(done))
        return Bundle(address=address, units=given, tables=rows(done))

    def fake_lookup(api, query, **options):
        seen["queries"].append((query, options.get("limit")))
        if query == "boom":
            raise AddressError("no address matched 'boom'")
        return fake_fetch(api, ADDRESS, units(1), **options)

    monkeypatch.setattr(pipeline, "fetch", fake_fetch)
    monkeypatch.setattr(pipeline, "lookup", fake_lookup)
    monkeypatch.setattr(store, "save", lambda path, tables: {"ejendomme": 1})
    monkeypatch.setattr(fetching, "POLITE_DELAY", 0)

    def run(queue: FetchQueue, on_change=None) -> dict:
        moves = []

        def changed() -> None:
            moves.append(queue.done)
            if on_change:
                on_change()

        queue.run(None, tmp_path / "x.duckdb", changed)
        return {**seen, "moves": moves}

    return run


# ── what is in it ───────────────────────────────────────────────────────


def test_a_job_knows_what_it_will_cost_before_it_runs():
    queue = FetchQueue()
    job = queue.add_units("Prøvegade 1", ADDRESS, units(3))
    assert job.expected == 3
    assert job.uuids == ["u0", "u1", "u2"]
    assert queue.total == 3 and queue.done == 0


def test_the_same_address_is_not_queued_twice_while_it_is_still_waiting():
    queue = FetchQueue()
    assert queue.add_query("Prøvegade 1") is not None
    assert queue.add_query("Prøvegade 1") is None
    queue.jobs[0].state = DONE
    assert queue.add_query("Prøvegade 1") is not None, "a finished job is not a bar"


def test_nothing_ticked_means_all_of_them():
    queue = FetchQueue()
    first = queue.add_units("a", ADDRESS, units(1))
    second = queue.add_units("b", ADDRESS, units(1))
    assert queue.chosen(None) == [first, second]
    assert queue.chosen(set()) == [first, second]
    assert queue.chosen({second.key}) == [second]


def test_auto_off_parks_a_job_instead_of_queueing_it():
    queue = FetchQueue()
    queue.auto = False
    job = queue.add_units("Prøvegade 1", ADDRESS, units(2))
    assert job.state == HELD
    assert queue.waiting == 0 and queue.held == 1
    assert not queue.active, "a parked job is not something to show a bar for"

    assert queue.start({job.key}) == 1
    assert job.state == WAITING and queue.active


# ── working through it ──────────────────────────────────────────────────


def test_a_run_fetches_every_unit_and_counts_them(drained):
    queue = FetchQueue()
    queue.add_units("Prøvegade 1", ADDRESS, units(3))
    seen = drained(queue)

    assert seen["units"] == [["u0", "u1", "u2"]]
    assert queue.jobs[0].state == DONE
    assert queue.done == queue.total == 3, "the bar has to reach the end"
    assert queue.rows == 1
    assert not queue.running and queue.current == ""


def test_progress_moves_while_the_run_is_going(drained):
    queue = FetchQueue()
    queue.add_units("Prøvegade 1", ADDRESS, units(3))
    moves = drained(queue)["moves"]
    # Called before each property and once at the end, so the bar creeps rather
    # than jumping from nothing to everything.
    assert moves == sorted(moves), "progress must never go backwards"
    assert moves[-1] == 3


def test_a_query_job_is_capped_where_it_was_asked_to_be(drained):
    queue = FetchQueue()
    queue.add_query("Prøvegade 1, 1. tv, 9999 Prøveby", limit=1)
    seen = drained(queue)
    assert seen["queries"] == [("Prøvegade 1, 1. tv, 9999 Prøveby", 1)]


def test_a_failure_is_recorded_and_does_not_stall_the_bar(drained):
    queue = FetchQueue()
    queue.add_query("boom")
    queue.add_units("Prøvegade 1", ADDRESS, units(2))
    drained(queue)

    failed, ran = queue.jobs
    assert failed.state == FAILED and "boom" in failed.note
    assert ran.state == DONE, "one failure must not stop what is queued behind it"
    # The failed job still comes off the bar, or progress sticks below 100%
    # for as long as the queue is on screen.
    assert queue.done == queue.total


def test_retrying_only_touches_what_failed(drained):
    queue = FetchQueue()
    queue.add_query("boom")
    queue.add_units("Prøvegade 1", ADDRESS, units(1))
    drained(queue)

    assert queue.failed == 1
    assert queue.retry(None) == 1
    assert queue.jobs[0].state == WAITING
    assert queue.jobs[1].state == DONE, "a job that worked is not retried"


def test_stopping_puts_the_unfinished_job_back_rather_than_losing_it(drained):
    queue = FetchQueue()
    queue.add_units("Prøvegade 1", ADDRESS, units(4))
    # Mid-run, not before it: run() clears the flag on the way in, so a stop
    # asked for while the queue is idle applies to no run at all.
    drained(queue, on_change=lambda: queue.done >= 2 and queue.stop())

    job = queue.jobs[0]
    assert job.state == WAITING, "what was not fetched must still be queued"
    assert "stopped partway" in job.note
    assert job.fetched < job.expected


def test_removing_leaves_what_is_in_flight_alone():
    queue = FetchQueue()
    running = queue.add_units("a", ADDRESS, units(1))
    waiting = queue.add_units("b", ADDRESS, units(1))
    running.state = fetching.RUNNING

    assert queue.remove(None) == 1
    assert queue.jobs == [running], "a job being fetched cannot be un-fetched"
    assert waiting not in queue.jobs


def test_a_second_worker_on_the_same_queue_is_a_no_op(drained, tmp_path):
    """`running` is set inside the worker, so a flag would not have caught this."""
    queue = FetchQueue()
    queue.add_units("Prøvegade 1", ADDRESS, units(1))

    entered = []

    def reentrant():
        # Called from inside the first run; the second must decline to start.
        if not entered:
            entered.append(True)
            queue.run(None, tmp_path / "x.duckdb", lambda: None)

    queue.run(None, tmp_path / "x.duckdb", reentrant)
    assert queue.jobs[0].fetched == 1, "the job must not have been fetched twice"


def test_what_a_job_wrote_is_remembered_for_exporting_it(drained):
    queue = FetchQueue()
    queue.add_query("Prøvegade 1")
    drained(queue)
    assert queue.uuids_for(None) == ["u0"]
