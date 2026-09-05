"""Find an address, see what the register holds at it, then queue what you want.

Two lists, and they are different kinds of list on purpose.

The first is DAWA's, and it only answers "which address did you mean". Picking
from it costs nothing and commits to nothing. Whole buildings come first there,
because the register indexes buildings and a building is usually what was meant:
asked about a house number, DAWA answers with a dozen of its flats, which
reads as a list of things to fetch and is not one - the register would be asked
the same question about any of them.

The second is the register's, and it is the real list: every legally registered
property at that address, which is one for a rented block and a hundred and
eighteen for a block of owner-occupied flats. That one is a multi-select, because
it is the list whose rows cost a request each.

Ticking rows and pressing f does not fetch them here. It hands them to the
application's queue - which fetches in the background, or holds them until the
queue screen starts them, depending on the auto-fetch setting. Either way a
building of a hundred flats can be handed over and left, while this screen goes
back to the top and looks up somewhere else.
"""

from __future__ import annotations

from rich.text import Text
from textual import on, work
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal
from textual.widgets import (
    Button,
    Footer,
    Header,
    Input,
    OptionList,
    SelectionList,
    Static,
)
from textual.widgets.option_list import Option
from textual.widgets.selection_list import Selection

from yaybo import pipeline
from yaybo.register.address import (
    AddressError,
    autocomplete,
    drop_unit,
    select_units,
)
from yaybo.register.fields import normalise
from yaybo.screens.base import YayboScreen
from yaybo.widgets.queue_bar import QueueBar
from yaybo.widgets.session_bar import SessionBar

# Long enough that a fast typist does not fire a request per keystroke, short
# enough that the list feels like it is keeping up.
SETTLE = 0.35
# How many addresses to ask DAWA for. Generous, because the buildings are pulled
# out of these, and one block of flats can otherwise crowd its neighbours off the
# list entirely.
MATCHES = 30
# How long to sit on a highlighted address before asking the register what is
# registered there. Longer than SETTLE on purpose: this one costs a request,
# and arrowing down a list should not fire one for every row passed over.
PROBE = 0.5

STEPS = ("1 · find the address", "2 · pick its properties", "3 · fetch")


class SearchScreen(YayboScreen):
    """Look an address up and fetch whichever of its properties you want."""

    BINDINGS = [
        Binding("escape", "back", "Back"),
        Binding("a", "select_all", "Select all"),
        Binding("n", "select_none", "Select none"),
        Binding("f", "fetch", "Queue ticked"),
        # Back one step, not out of the screen. Safe as a screen binding
        # because Input binds `left` itself for the text cursor, so it only
        # ever reaches here from one of the two lists.
        Binding("left", "back_a_step", "Addresses", show=False),
        Binding("ctrl+r", "reset", "Start over"),
        # From the text box into the list. Only fires while the box has focus:
        # both lists bind `down` themselves, so it never reaches here from them.
        Binding("down", "to_matches", "To the list", show=False),
    ]

    def __init__(self, query: str = "") -> None:
        super().__init__()
        # Not `self.query`: that is Widget.query, and shadowing it breaks
        # everything Textual does by selector, including auto-focus.
        self.typed = query
        self.matches: list[dict] = []
        self.address: dict | None = None
        self.units: list[dict] = []
        self._settling = None
        self._probing = None
        self._capped = False
        # What the register holds at each building already asked about, keyed
        # the way find_units queries: postcode, street, number. That query
        # ignores floor and door entirely, so one lookup answers for a building
        # and every flat in it - but they are different answers. The building
        # row costs all of them; a flat row costs the one that matches it, so
        # the units are kept rather than a count, and each row works its own
        # number out of them.
        self.held: dict[tuple[str, str, str], list[dict]] = {}
        # Header rows and matches together, in display order.
        self.rows: list[dict | str] = []

    def compose(self) -> ComposeResult:
        yield Header()
        yield SessionBar()
        # This screen is the only one that reaches the register, and the only
        # one that adds to the library. Saying so at the top is what separates
        # it from the library's filter box, which looks the same and is not.
        yield Static("＋  Find a new property", id="search-heading")
        yield Static("", id="search-steps")
        yield Input(
            value=self.typed,
            placeholder="Prøvegade 1, 9999 Prøveby",
            id="search-input",
        )
        yield Static("", id="search-status")
        yield OptionList(id="search-matches")
        # Which building's properties are being ticked. Every row in the list
        # below can read identically - a block of flats registered without
        # floors is a dozen rows of the same street address - so the one thing
        # that says where you are has to sit outside the list.
        yield Static("", id="search-context")
        yield SelectionList(id="search-units")
        # Everything the keys do, as buttons, for anyone not driving this from
        # the keyboard. Shown only while there is a list to act on.
        with Horizontal(id="search-actions"):
            yield Button("◀  Addresses", id="search-back-btn")
            yield Button("Select all", id="search-all")
            yield Button("Select none", id="search-none")
            yield Button("＋  Add to queue", id="search-queue", variant="primary")
        yield QueueBar()
        yield Footer()

    def on_mount(self) -> None:
        for hidden in ("#search-matches", "#search-units", "#search-context",
                       "#search-actions"):
            self.query_one(hidden).display = False
        self._steps(1)
        self._say(
            "Type an address to look it up in the land register. DAWA will "
            "clean up the spelling, the spacing and the floor, so a rough one "
            "is fine."
        )
        self.query_one("#search-input", Input).focus()
        if self.typed:
            self._lookup(self.typed)

    def _say(self, message: str) -> None:
        self.query_one("#search-status", Static).update(message)

    def _steps(self, active: int) -> None:
        """Show which of the three stages this screen is at.

        The screen has two lists that look alike and mean different things, so
        which one is on show is the single most useful thing to state.
        """
        line = Text()
        for number, label in enumerate(STEPS, start=1):
            if number > 1:
                line.append("  →  ", style="dim")
            line.append(
                label, style="bold" if number == active else "dim"
            )
        self.query_one("#search-steps", Static).update(line)

    @property
    def _palette(self) -> tuple[str, str]:
        """The two colours the address list annotates itself with.

        Read off the live theme rather than written down here, so the red and
        the amber stay the theme's red and amber if it is ever changed.
        """
        theme = self.app.current_theme
        # A theme is allowed to leave these unset, so fall back to the
        # terminal's own red and yellow rather than to no colour at all.
        return theme.error or "red", theme.warning or "yellow"

    @property
    def _picking_units(self) -> bool:
        return bool(self.units) and self.query_one("#search-units").display

    # ── step one: which address ─────────────────────────────────────────

    @on(Input.Changed, "#search-input")
    def _typing(self, event: Input.Changed) -> None:
        if self._settling is not None:
            self._settling.stop()
        self.address = None
        self.units = []
        self.query_one("#search-units").display = False
        self._settling = self.set_timer(SETTLE, lambda: self._lookup(event.value))

    @work(thread=True, exclusive=True, group="autocomplete")
    def _lookup(self, query: str) -> None:
        found = autocomplete(query, MATCHES) if len(query.strip()) >= 3 else []
        self.app.call_from_thread(self._show_matches, found, query)

    def _show_matches(self, found: list[dict], query: str) -> None:
        self.matches = _buildings_first(found)
        self.rows = _in_sections(self.matches)
        matches = self.query_one("#search-matches", OptionList)
        matches.clear_options()
        matches.add_options(self._options())
        # A freshly filled list has no cursor, and a list with no cursor eats
        # the first key aimed at it. Put it on the first row that can be
        # chosen, which is a building and the row most likely to be wanted.
        matches.highlighted = self._first_choosable()
        matches.display = bool(self.matches)
        self.query_one("#search-units").display = False
        self._show_actions(False)

        if not self.matches:
            if len(query.strip()) >= 3:
                self._say(f"DAWA knows no address like {query.strip()!r}.")
            return

        self._capped = len(found) >= MATCHES
        self._describe_matches()

    def _options(self) -> list[Option]:
        palette = self._palette
        out = []
        for row in self.rows:
            if isinstance(row, str):
                # A heading, not a choice. Disabled so that arrowing through
                # the list steps over it rather than landing on something that
                # does nothing when you press enter.
                out.append(Option(Text(row, style="bold dim"), disabled=True))
                continue
            out.append(Option(_match_label(row, self._cost(row), palette)))
        return out

    def _first_choosable(self) -> int | None:
        for index, row in enumerate(self.rows):
            if not isinstance(row, str):
                return index
        return None

    def _match_at(self, index: int) -> dict | None:
        try:
            row = self.rows[index]
        except IndexError:
            return None
        return None if isinstance(row, str) else row

    def _cost(self, match: dict) -> tuple[int, bool] | None:
        """What choosing this row would fetch: how many, and whether that is
        the whole building rather than the flat that was asked for.

        None while the register has not been asked. The building row and its
        flats share one answer from the register and read entirely different
        numbers off it - four properties for the building, one for the flat -
        which is the whole reason this is computed per row rather than cached
        as a count.
        """
        units = self.held.get(_building_key(match))
        if units is None:
            return None
        if not units:
            return (0, False)
        if not match["etage"] and not match["doer"]:
            return (len(units), False)
        picked, warning = select_units(units, match["etage"], match["doer"])
        return (len(picked), bool(warning))

    def _describe_matches(self) -> None:
        """The line above the address list, re-stated as answers come back.

        Deliberately spelled out as a sequence. While the box has focus a
        letter is a letter - addresses contain "30A" - so `a` cannot mean
        anything until the cursor is in the list.
        """
        capped = f" (DAWA's first {MATCHES})" if self._capped else ""
        dead = self._dead_ends()
        # Only ever counts what the register has actually answered for, so this
        # says "2 of these are empty", never "2 might be".
        empty = f" · {dead} with nothing tinglyst" if dead else ""
        self._say(
            f"{len(self.matches)} address(es){capped}{empty}.\n"
            "↓ or enter moves to the list · then enter opens one, "
            "or a takes its whole building"
        )

    @on(Input.Submitted, "#search-input")
    def _submitted(self) -> None:
        """Enter in the box moves to the list, which is where the choice is."""
        if self.matches:
            matches = self.query_one("#search-matches", OptionList)
            matches.focus()
            matches.highlighted = self._first_choosable()

    @on(OptionList.OptionSelected, "#search-matches")
    def _chose_address(self, event: OptionList.OptionSelected) -> None:
        match = self._match_at(event.option_index)
        if match is not None:
            self._open(match)

    def _highlighted_match(self) -> dict | None:
        """The address under the cursor, or the first one if there is no cursor.

        Falling back to the first row rather than refusing: someone who has just
        typed an address and pressed `a` means the address they typed, and being
        told to pick one first when there is an obvious one is no answer.
        """
        if not self.matches:
            return None
        matches = self.query_one("#search-matches", OptionList)
        index = matches.highlighted
        if index is None:
            return self.matches[0]
        return self._match_at(index) or self.matches[0]

    # ── asking ahead, so a dead end is visible before it is walked into ──

    @on(OptionList.OptionHighlighted, "#search-matches")
    def _match_moved(self, event: OptionList.OptionHighlighted) -> None:
        """Ask what is registered at the address under the cursor.

        DAWA answers "is this a real address", which is a different question
        from "does the land register hold anything there" - and plenty of real
        addresses are registered as part of a neighbouring property, or not at
        all. Asking while the cursor rests on a row means the answer is usually
        already on screen by the time enter is pressed.
        """
        if self._picking_units or not self.matches:
            return
        match = self._match_at(event.option_index)
        if match is None or _building_key(match) in self.held:
            return
        self._stop_probing()
        self._probing = self.set_timer(PROBE, lambda: self._probe(match))

    def _stop_probing(self) -> None:
        if self._probing is not None:
            self._probing.stop()
            self._probing = None

    @work(thread=True, exclusive=True, group="probe")
    def _probe(self, match: dict) -> None:
        key = _building_key(match)
        if key in self.held:
            return
        try:
            units, _ = pipeline.units_at(
                self.app.api, {**match, "etage": "", "doer": ""}
            )
        except Exception:  # noqa: BLE001
            # A probe is a courtesy, not the search. If the register will not
            # answer, leave the row unannotated and let enter find out properly
            # - failing here must never cost the user their place in the list.
            return
        self.app.call_from_thread(self._learned, key, units)

    def _learned(self, key: tuple[str, str, str], units: list[dict]) -> None:
        self.held[key] = units
        if not self._picking_units:
            self._relabel()
            if self.matches:
                self._describe_matches()

    def _relabel(self) -> None:
        """Rewrite the address rows in place from what the cache now knows.

        In place rather than rebuilt: clearing and refilling the list would
        drop the cursor and the scroll position, and this runs while the user
        is reading the very row it is annotating.
        """
        matches = self.query_one("#search-matches", OptionList)
        if not self.rows or matches.option_count != len(self.rows):
            return
        palette = self._palette
        for index, row in enumerate(self.rows):
            if isinstance(row, str):
                continue
            cost = self._cost(row)
            matches.replace_option_prompt_at_index(
                index, _match_label(row, cost, palette)
            )
            # A row with nothing behind it is not a choice, so it stops being
            # selectable as well as looking spent. Textual skips disabled rows
            # when arrowing, which is exactly "do not go in there".
            if cost is not None and cost[0] == 0:
                matches.disable_option_at_index(index)

    def _dead_ends(self) -> int:
        return sum(
            1
            for match in self.matches
            if self.held.get(_building_key(match)) == []
        )

    def _open(
        self, address: dict, *, whole_building: bool = False, take_all: bool = False
    ) -> None:
        """Ask the register what sits at an address, then show it to choose from."""
        if whole_building:
            # The register searches at building level anyway; dropping the floor
            # is what stops select_units narrowing straight back to one flat.
            address = {
                **address,
                "etage": "",
                "doer": "",
                "tekst": drop_unit(address["tekst"]),
            }
        # Choosing an address settles the question a pending probe was going to
        # answer, so it stops being worth a request to the register.
        self._stop_probing()
        if self.held.get(_building_key(address)) == []:
            self._say(
                f"{address['tekst']} holds nothing in the land register - "
                "already checked, so there is nothing to fetch. Pick another "
                "of the addresses below."
            )
            return
        self.address = address
        self._say(f"Asking the register what is registered at {address['tekst']}…")
        self._find_units(address, take_all)

    # ── step two: which properties ──────────────────────────────────────

    @work(thread=True, exclusive=True, group="units")
    def _find_units(self, address: dict, take_all: bool) -> None:
        try:
            units, warning = pipeline.units_at(self.app.api, address)
        except AddressError as error:
            self.app.call_from_thread(self._say, str(error))
            return
        except Exception as error:  # noqa: BLE001 - shown to the user verbatim
            self.app.call_from_thread(
                self._say, f"The register would not answer: {error}"
            )
            return
        self.app.call_from_thread(self._show_units, units, warning, take_all)

    def _show_units(
        self, units: list[dict], warning: str, take_all: bool = False
    ) -> None:
        self.units = units
        if self.address is not None and units:
            # What came back is the answer for the whole building only when the
            # building is what was asked for; a flat's narrowed answer must not
            # be cached as the building's.
            key = _building_key(self.address)
            if key not in self.held and not self.address["etage"]:
                self.held[key] = units
        if not units:
            self._nothing_here()
            return

        listing = self.query_one("#search-units", SelectionList)
        listing.clear_options()
        # One registered property is not a choice, so it arrives ticked. Several
        # are, so they do not - unless the whole building was what was asked for.
        ticked = take_all or len(units) == 1
        listing.add_options(
            [
                Selection(_unit_label(unit, index + 1), index, ticked)
                for index, unit in enumerate(units)
            ]
        )
        # Same again: without this the first space lands on nothing at all.
        listing.highlighted = 0
        listing.display = True
        self.query_one("#search-matches").display = False
        self._name_building(
            self.address["tekst"] if self.address else "", len(units)
        )
        self._show_actions(True)
        self._steps(2)
        self._describe_selection(warning)
        listing.focus()

    def _name_building(self, where: str, held: int) -> None:
        """Say whose properties these are, above the list of them.

        Not `_context`: MessagePump has one of those and drives its whole
        message loop through it, so a screen that defines its own stops
        processing messages entirely. Same trap as `self.query`.
        """
        line = Text("Inside  ", style="dim")
        line.append(where, style="bold")
        line.append(
            f"   ·   {held} registered propert{'y' if held == 1 else 'ies'}",
            style="dim",
        )
        panel = self.query_one("#search-context", Static)
        panel.update(line)
        panel.display = bool(where)

    def _show_actions(self, on: bool) -> None:
        self.query_one("#search-actions").display = on
        if not on:
            self.query_one("#search-context").display = False

    def _nothing_here(self) -> None:
        """An address that exists, with no registered property behind it.

        Stays on the address list rather than clearing the screen: the answer
        to "nothing here" is nearly always the row above or below, and the row
        just tried is now struck through, so the same dead end cannot be walked
        into twice.
        """
        where = self.address["tekst"] if self.address else "that address"
        if self.address is not None:
            self.held[_building_key(self.address)] = []
        self._show_actions(False)
        self._relabel()
        matches = self.query_one("#search-matches", OptionList)
        matches.display = bool(self.matches)
        self.query_one("#search-units").display = False
        self._steps(1)
        left = len(self.matches) - self._dead_ends()
        nothing = (
            "Nothing on this list has anything registered - try another address."
            if self.matches and left <= 0
            else "Struck-through rows are the ones already known to be empty."
        )
        self._say(
            f"Nothing is tinglyst at {where}.\n"
            f"The address is real, but the land register holds no property "
            f"there. {nothing}"
        )
        if self.matches:
            matches.focus()

    @on(SelectionList.SelectedChanged, "#search-units")
    def _selection_changed(self) -> None:
        if self._picking_units:
            self._describe_selection()

    def _describe_selection(self, warning: str = "") -> None:
        listing = self.query_one("#search-units", SelectionList)
        chosen, total = len(listing.selected), len(self.units)
        held = "property" if total == 1 else "properties"
        note = f"{warning}\n" if warning else ""
        self._say(
            f"{note}{total} {held} registered here · {chosen} ticked\n"
            "space ticks one · a ticks all · n clears · "
            "f queues them · ← back to the addresses"
        )

    # ── the keys that mean different things at each step ────────────────

    def action_to_matches(self) -> None:
        """Move from the address box down into the list of matches."""
        if not self.matches or self._picking_units:
            return
        matches = self.query_one("#search-matches", OptionList)
        matches.focus()
        matches.highlighted = 0

    def action_select_all(self) -> None:
        if self._picking_units:
            self.query_one("#search-units", SelectionList).select_all()
            return
        # Still on the address list, where "all" means everything at this
        # address - so take the building, and arrive with the lot ticked.
        match = self._highlighted_match()
        if match is None:
            self._say("Type an address first. a then takes its whole building.")
            return
        self._open(match, whole_building=True, take_all=True)

    def action_select_none(self) -> None:
        if self._picking_units:
            self.query_one("#search-units", SelectionList).deselect_all()

    def action_fetch(self) -> None:
        """Put the ticked properties in the queue and carry on."""
        if not self._picking_units:
            self._say(
                "Pick an address first - enter on one, or a for its whole building."
            )
            return
        listing = self.query_one("#search-units", SelectionList)
        chosen = sorted(listing.selected)
        if not chosen:
            self._say(
                "Nothing ticked. space ticks the one under the cursor; a ticks all."
            )
            return
        if self.address is None:
            return

        units = [self.units[index] for index in chosen]
        self.app.enqueue_units(self.address["tekst"], self.address, units)
        if not self.app.logged_in:
            self.notify(
                "Queued against the public register. ctrl+L logs in, for owners' "
                "dates of birth and the chain of previous owners."
            )
        # Unticked on the way out, so the same rows cannot be queued twice by
        # pressing f again without meaning to.
        listing.deselect_all()
        held = "property" if len(units) == 1 else "properties"
        note = self.app.queued_note()
        # After the refresh, not now: deselect_all posts SelectedChanged, which
        # arrives once this handler has returned and rewrites the status line
        # with "0 ticked". Saying this last is what makes it the thing left on
        # screen rather than the thing flashed and overwritten.
        self.call_after_refresh(
            self._say,
            f"Queued {len(units)} {held} from {self.address['tekst']}.\n"
            f"{note}  ← goes back to the addresses to queue more, "
            "b shows the queue, l the library.",
        )
        self.notify(f"Queued {len(units)} {held}. {note}")

    # ── the buttons, which do exactly what the keys do ──────────────────

    @on(Button.Pressed, "#search-back-btn")
    def _pressed_back(self) -> None:
        self.action_back_a_step()

    @on(Button.Pressed, "#search-all")
    def _pressed_all(self) -> None:
        self.action_select_all()

    @on(Button.Pressed, "#search-none")
    def _pressed_none(self) -> None:
        self.action_select_none()

    @on(Button.Pressed, "#search-queue")
    def _pressed_queue(self) -> None:
        self.action_fetch()

    # ── getting out ─────────────────────────────────────────────────────

    def action_back_a_step(self) -> None:
        """From the properties back to the addresses, keeping what was typed.

        A step back, not a way out: escape leaves the screen entirely, and
        conflating the two means one keypress can lose a search that took
        three requests to build.
        """
        if not self._picking_units:
            self.action_back()
            return
        self.units = []
        self.address = None
        self.query_one("#search-units").display = False
        self._show_actions(False)
        matches = self.query_one("#search-matches", OptionList)
        matches.display = bool(self.matches)
        self._steps(1)
        if self.matches:
            self._describe_matches()
            matches.focus()
        else:
            self.query_one("#search-input", Input).focus()

    def action_reset(self) -> None:
        self._stop_probing()
        self.address = None
        self.units = []
        self.query_one("#search-units").display = False
        self.query_one("#search-matches").display = bool(self.matches)
        field = self.query_one("#search-input", Input)
        field.value = ""
        field.focus()
        self._steps(1)
        self._say("Type an address to look it up in the land register.")

    def action_back(self) -> None:
        # Leaving no longer stops anything: the queue belongs to the
        # application, and walking away from this screen is the whole point of
        # it having been queued rather than fetched here.
        self.app.action_library()


def _buildings_first(found: list[dict]) -> list[dict]:
    """A row per building at the top, then the individual flats DAWA matched."""
    buildings: dict[tuple, dict] = {}
    units: list[dict] = []
    for match in found:
        key = (match["vejnavn"], match["husnummer"], match["postnummer"])
        buildings.setdefault(
            key, {**match, "etage": "", "doer": "", "tekst": drop_unit(match["tekst"])}
        )
        if match["etage"] or match["doer"]:
            units.append(match)
    return list(buildings.values()) + units


def _building_key(match: dict) -> tuple[str, str, str]:
    """What find_units actually keys on, which is not the whole address."""
    return (
        str(match.get("postnummer", "")),
        normalise(match.get("vejnavn", "")),
        str(match.get("husnummer", "")),
    )


# The two halves of the address list. They are different kinds of choice, not
# a ranking: the first fetches every property at an address, the second fetches
# one flat. Keeping them apart is what stops a flat's row being read as though
# its number belonged to the building.
BUILDINGS = "HELE BYGNINGER   ·   every property registered at the address"
UNITS = "ENKELTE BOLIGER   ·   only the one flat"


def _in_sections(matches: list[dict]) -> list[dict | str]:
    """The matches with a heading above each kind, in display order."""
    buildings = [m for m in matches if not m["etage"] and not m["doer"]]
    units = [m for m in matches if m["etage"] or m["doer"]]
    rows: list[dict | str] = []
    if buildings:
        rows.append(BUILDINGS)
        rows.extend(buildings)
    if units:
        rows.append(UNITS)
        rows.extend(units)
    return rows


def _match_label(
    match: dict, cost: tuple[int, bool] | None, palette: tuple[str, str]
) -> Text:
    """One row of the address list, annotated once the register has answered.

    `cost` is (how many properties this row would fetch, whether that is the
    whole building rather than the flat asked for), or None while we have not
    asked. Nothing is invented: an unasked row looks exactly as it did before,
    and only a real answer from the register earns a row a number or a strike.
    """
    label = Text(match["tekst"])
    if cost is None:
        return label
    count, fell_back = cost
    empty, some = palette
    if count == 0:
        label.stylize("strike")
        label.append("   intet tinglyst her", style=f"bold {empty}")
    elif fell_back:
        # The register has no separate entry for this flat, so picking it gets
        # the building. Saying so here is the difference between an honest row
        # and one that promises a flat and delivers ninety.
        label.append(
            f"   ingen egen ejendom · hele bygningen: {count}", style=f"bold {empty}"
        )
    else:
        label.append(
            f"   {count} ejendom{'' if count == 1 else 'me'}", style=f"bold {some}"
        )
    return label


def _unit_label(unit: dict, number: int) -> Text:
    """One registered property, numbered so two of them can be told apart.

    A block registered without floor numbers gives every row the same street
    address, which reads as the list having repeated itself. The number is the
    one thing guaranteed to differ; the rest is whatever the register happens
    to hold that distinguishes them.
    """
    label = Text(f"{number:>3}  ", style="dim")
    label.append(unit.get("adresse", ""))
    detail = "  ·  ".join(
        str(unit[key])
        for key in ("ejendomstype", "ejerlejlighedsnr", "bfe_nr", "matrikel")
        if unit.get(key)
    )
    if detail:
        label.append(f"   {detail}", style="dim")
    return label
