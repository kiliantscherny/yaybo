"""Find an address, see what the register holds at it, then fetch what you want.

Two lists, and they are different kinds of list on purpose.

The first is DAWA's, and it only answers "which address did you mean". Picking
from it costs nothing and commits to nothing. Whole buildings come first there,
because the register indexes buildings and a building is usually what was meant:
asked about "Prøvegade 1", DAWA answers with a dozen of its flats, which
reads as a list of things to fetch and is not one - the register would be asked
the same question about any of them.

The second is the register's, and it is the real list: every legally registered
property at that address, which is one for a rented block and a hundred and
eighteen for a block of owner-occupied flats. That one is a multi-select, because
it is the list whose rows cost a request each.
"""

from __future__ import annotations

import threading

from rich.text import Text
from textual import on, work
from textual.app import ComposeResult
from textual.binding import Binding
from textual.widgets import Footer, Header, Input, OptionList, SelectionList, Static
from textual.widgets.selection_list import Selection

from yaybo import pipeline, store
from yaybo.register.address import AddressError, autocomplete, drop_unit
from yaybo.screens.base import YayboScreen

# Long enough that a fast typist does not fire a request per keystroke, short
# enough that the list feels like it is keeping up.
SETTLE = 0.35
# How many addresses to ask DAWA for. Generous, because the buildings are pulled
# out of these, and one block of flats can otherwise crowd its neighbours off the
# list entirely.
MATCHES = 30


class SearchScreen(YayboScreen):
    """Look an address up and fetch whichever of its properties you want."""

    BINDINGS = [
        Binding("escape", "back", "Back"),
        Binding("a", "select_all", "Select all"),
        Binding("n", "select_none", "Select none"),
        Binding("f", "fetch", "Fetch ticked"),
        Binding("ctrl+c", "stop", "Stop fetching", priority=True),
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
        # A building can hold a hundred properties, and a hundred properties is
        # a couple of minutes. Checked between them, so stopping never abandons
        # one half-fetched and never loses what is already gathered.
        self._stop = threading.Event()

    def compose(self) -> ComposeResult:
        yield Header()
        yield Input(
            value=self.typed,
            placeholder="Prøvegade 1, 9999 Prøveby",
            id="search-input",
        )
        yield Static("", id="search-status")
        yield OptionList(id="search-matches")
        yield SelectionList(id="search-units")
        yield Footer()

    def on_mount(self) -> None:
        for hidden in ("#search-matches", "#search-units"):
            self.query_one(hidden).display = False
        self._say(
            "Type an address. DAWA will clean up the spelling, the spacing and "
            "the floor, so a rough one is fine."
        )
        self.query_one("#search-input", Input).focus()
        if self.typed:
            self._lookup(self.typed)

    def _say(self, message: str) -> None:
        self.query_one("#search-status", Static).update(message)

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
        matches = self.query_one("#search-matches", OptionList)
        matches.clear_options()
        matches.add_options([_match_label(match) for match in self.matches])
        # A freshly filled list has no cursor, and a list with no cursor eats
        # the first key aimed at it. Put it on the first row, which is the
        # building and the row most likely to be wanted.
        matches.highlighted = 0 if self.matches else None
        matches.display = bool(self.matches)
        self.query_one("#search-units").display = False

        if not self.matches:
            if len(query.strip()) >= 3:
                self._say(f"DAWA knows no address like {query.strip()!r}.")
            return

        capped = f" (DAWA's first {MATCHES})" if len(found) >= MATCHES else ""
        # Deliberately spelled out as a sequence. While the box has focus a
        # letter is a letter - addresses contain "30A" - so `a` cannot mean
        # anything until the cursor is in the list.
        self._say(
            f"{len(self.matches)} address(es){capped}.\n"
            "↓ or enter moves to the list · then enter opens one, "
            "or a takes its whole building"
        )

    @on(Input.Submitted, "#search-input")
    def _submitted(self) -> None:
        """Enter in the box moves to the list, which is where the choice is."""
        if self.matches:
            matches = self.query_one("#search-matches", OptionList)
            matches.focus()
            matches.highlighted = 0

    @on(OptionList.OptionSelected, "#search-matches")
    def _chose_address(self, event: OptionList.OptionSelected) -> None:
        self._open(self.matches[event.option_index])

    def _highlighted_match(self) -> dict | None:
        """The address under the cursor, or the first one if there is no cursor.

        Falling back to the first row rather than refusing: someone who has just
        typed an address and pressed `a` means the address they typed, and being
        told to pick one first when there is an obvious one is no answer.
        """
        if not self.matches:
            return None
        matches = self.query_one("#search-matches", OptionList)
        index = 0 if matches.highlighted is None else matches.highlighted
        try:
            return self.matches[index]
        except IndexError:
            return self.matches[0]

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
        listing = self.query_one("#search-units", SelectionList)
        listing.clear_options()
        # One registered property is not a choice, so it arrives ticked. Several
        # are, so they do not - unless the whole building was what was asked for.
        ticked = take_all or len(units) == 1
        listing.add_options(
            [
                Selection(_unit_label(unit), index, ticked)
                for index, unit in enumerate(units)
            ]
        )
        # Same again: without this the first space lands on nothing at all.
        listing.highlighted = 0 if units else None
        listing.display = bool(units)
        self.query_one("#search-matches").display = False

        if not units:
            self._say("The register has nothing registered at that address.")
            return
        self._describe_selection(warning)
        listing.focus()

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
            "space ticks one · a ticks all · n clears · f fetches the ticked ones"
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
        self._fetch([self.units[index] for index in chosen])

    # ── step three: fetch ───────────────────────────────────────────────

    def _fetch(self, units: list[dict]) -> None:
        if self.address is None:
            return
        self._stop.clear()
        if len(units) > 1:
            self._say(f"Fetching {len(units)} properties - ctrl+C stops it.")
        if not self.app.logged_in:
            self.notify(
                "Fetching from the public register. ctrl+L logs in first, for "
                "owners' dates of birth and the chain of previous owners."
            )
        self._run_fetch(self.address, units)

    @work(thread=True, exclusive=True, group="fetch")
    def _run_fetch(self, address: dict, units: list[dict]) -> None:
        def progress(index: int, total: int, unit: dict) -> None:
            self.app.call_from_thread(
                self._say,
                f"Fetching {index} of {total}: {unit.get('adresse', '')}\n"
                "ctrl+C stops after the one in flight.",
            )

        try:
            bundle = pipeline.fetch(
                self.app.api,
                address,
                units,
                delay=0.6 if len(units) > 1 else 0,
                on_unit=progress,
                should_stop=self._stop.is_set,
            )
            written = store.save(self.app.database, bundle.tables)
        except Exception as error:  # noqa: BLE001 - shown to the user verbatim
            self.app.call_from_thread(self._say, f"That did not work: {error}")
            self.app.call_from_thread(
                self.notify, f"Fetch failed: {error}", severity="error"
            )
            return

        rows = sum(written.values())
        found = bundle.properties
        stopped = " (stopped early)" if self._stop.is_set() else ""
        self.app.call_from_thread(
            self.notify,
            f"Fetched {len(found)} propert{'y' if len(found) == 1 else 'ies'}, "
            f"{rows} rows, into {self.app.database}{stopped}",
        )
        self.app.call_from_thread(self._done, [row["uuid"] for row in found])

    def _done(self, uuids: list[str]) -> None:
        """One property opens; several go back to the library, which lists them."""
        from yaybo.screens.property import PropertyScreen

        if len(uuids) == 1:
            self.app.push_screen(PropertyScreen(uuids[0]))
            return
        self._say(f"Fetched {len(uuids)} properties - press l for the library.")
        self.app.action_library()

    # ── getting out ─────────────────────────────────────────────────────

    def action_stop(self) -> None:
        if self._stop.is_set():
            return
        self._stop.set()
        self._say("Stopping after the property being fetched now…")

    def action_reset(self) -> None:
        self.address = None
        self.units = []
        self.query_one("#search-units").display = False
        self.query_one("#search-matches").display = bool(self.matches)
        field = self.query_one("#search-input", Input)
        field.value = ""
        field.focus()
        self._say("Type an address.")

    def action_back(self) -> None:
        # A fetch left running would keep writing to a database the user has
        # walked away from, so leaving stops it.
        self._stop.set()
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


def _match_label(match: dict) -> Text:
    label = Text(match["tekst"])
    if not match["etage"] and not match["doer"]:
        label.append("   hele bygningen", style="dim")
    return label


def _unit_label(unit: dict) -> Text:
    label = Text(unit.get("adresse", ""))
    if unit.get("ejendomstype"):
        label.append(f"   {unit['ejendomstype']}", style="dim")
    return label
