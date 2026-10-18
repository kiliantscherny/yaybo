"""The address register, with the network stubbed.

Adressevælger answers a search with rendered strings rather than fields, so most
of what can go wrong here is in reading them back. Every answer below has the
shape the live service gives, with the addresses invented.
"""

from __future__ import annotations

import pytest

from yaybo.register import address

BUILDING = "aaaaaaaa-0000-0000-0000-000000000001"


def _stub(monkeypatch, answers: dict[str, dict]):
    """Answer each path with a canned payload, and record what was asked."""
    asked: list[tuple[str, dict]] = []

    def fake_get(path: str, **params) -> dict:
        asked.append((path, params))
        return answers.get(path, {"status": "ok", "fund": []})

    monkeypatch.setattr(address, "_get", fake_get)
    return asked


def _hit(titel: str, kind: str = "adresse", **extra) -> dict:
    return {"type": kind, "id": f"id-{titel}", "titel": titel, **extra}


def test_a_rendered_address_reads_back_into_its_fields():
    found = address._from_titel("Prøvegade 12B, 3. tv, 9999 Prøveby")
    assert found == {
        "tekst": "Prøvegade 12B, 3. tv, 9999 Prøveby",
        "vejnavn": "Prøvegade",
        "husnummer": "12B",
        "postnummer": "9999",
        "etage": "3",
        "doer": "tv",
    }
    building = address._from_titel("Lille Prøvevej 1, 9999 Prøveby")
    assert (building["vejnavn"], building["etage"], building["doer"]) == (
        "Lille Prøvevej", "", ""
    )


def test_a_village_name_is_not_read_as_a_floor():
    """The supplerende bynavn sits where a unit would, and is neither."""
    found = address._from_titel("Prøvegade 1, Lilleby, 9999 Prøveby")
    assert (found["etage"], found["doer"]) == ("", "")
    assert found["tekst"] == "Prøvegade 1, 9999 Prøveby"
    flat = address._from_titel("Prøvegade 1, st. tv, Lilleby, 9999 Prøveby")
    assert (flat["etage"], flat["doer"]) == ("st", "tv")
    # A door with no floor is still a unit, not a town, so it is kept.
    assert address._from_titel("Prøvegade 1, tv, 9999 Prøveby")["etage"] == "tv"


def test_utm_metres_become_degrees():
    """Copenhagen City Hall's adgangspunkt, which is a public landmark."""
    laengdegrad, breddegrad = address.utm32_to_degrees(724434.93, 6175755.61)
    assert laengdegrad == pytest.approx(12.56958, abs=1e-4)
    assert breddegrad == pytest.approx(55.67563, abs=1e-4)


def test_a_washed_match_is_trusted_without_searching(monkeypatch):
    asked = _stub(monkeypatch, {"vask/": {
        "vaskestatus": {"kode": 1000, "tekst": "Eksakt match"},
        "vaskeresultat": {"adressebetegnelse": "Prøvegade 1, 2., 9999 Prøveby"},
    }})
    found = address.resolve_address("provegade 1 2 sal 9999")
    assert (found["vejnavn"], found["husnummer"], found["etage"]) == (
        "Prøvegade", "1", "2"
    )
    assert [path for path, _ in asked] == ["vask/"]


def test_a_refused_wash_falls_through_to_the_search(monkeypatch):
    """A negative code is a reason, not an address, and the search gets a go."""
    _stub(monkeypatch, {
        "vask/": {
            "vaskestatus": {"kode": -800, "tekst": "Vejnavn findes ikke"},
            "vaskeresultat": {"adressebetegnelse": None},
        },
        "adresser/soeg": {"status": "ok", "fund": [
            _hit("Prøvegade 1, 9999 Prøveby"),
            _hit("Prøvegade 1, 1., 9999 Prøveby"),
        ]},
    })
    found = address.resolve_address("Provegade 1, 9999")
    assert found["tekst"] == "Prøvegade 1, 9999 Prøveby"
    assert not found["etage"]


def test_two_buildings_are_ambiguous(monkeypatch):
    _stub(monkeypatch, {
        "vask/": {"vaskestatus": {"kode": -300}, "vaskeresultat": {}},
        "adresser/soeg": {"status": "ok", "fund": [
            _hit("Prøvegade 1, 9999 Prøveby"),
            _hit("Prøvegade 1, 8888 Andetby"),
        ]},
    })
    with pytest.raises(address.AddressError, match="ambiguous"):
        address.resolve_address("Prøvegade 1")


def test_a_building_keeps_only_its_own_flats_and_is_placed_once(monkeypatch):
    """The search is fuzzy, so number 10 and 1A must not be taken for 1."""
    asked = _stub(monkeypatch, {
        "adresser/soeg": {"status": "ok", "fund": [
            _hit("Prøvegade 1, 9999 Prøveby", husnummerId=BUILDING),
            _hit("Prøvegade 1, st. tv, 9999 Prøveby", husnummerId=BUILDING),
            _hit("Prøvegade 1, 1. th, 9999 Prøveby", husnummerId=BUILDING),
            _hit("Prøvegade 10, 9999 Prøveby", husnummerId="other"),
            _hit("Prøvegade 1A, 9999 Prøveby", husnummerId="other"),
            _hit("Prøvegade 1, 8888 Andetby", husnummerId="other"),
            _hit("Prøvegade 1, 2., Lilleby, 9999 Prøveby", husnummerId=BUILDING),
        ]},
        f"husnumre/{BUILDING}": {"status": "ok", "husnummer": {
            "adgangspunkt": {"koordinater": {"x": 724434.93, "y": 6175755.61}},
        }},
    })
    found = address.dawa_addresses(
        {"vejnavn": "Prøvegade", "husnummer": "1", "postnummer": "9999"}
    )
    assert sorted(found) == [("", ""), ("1", "th"), ("2", ""), ("st", "tv")]
    flat = found[("1", "th")]
    assert flat["uuid"] == "id-Prøvegade 1, 1. th, 9999 Prøveby"
    assert flat["husnummer"] == BUILDING
    assert flat["breddegrad"] == pytest.approx(55.6756, abs=1e-3)
    # One search and one placing, however many flats there are.
    assert [path for path, _ in asked] == ["adresser/soeg", f"husnumre/{BUILDING}"]


def test_a_street_in_several_towns_is_not_listed_without_a_postcode(monkeypatch):
    asked = _stub(monkeypatch, {"husnumre/soeg": {"status": "ok", "fund": [
        {"type": "navngivenvejpostnummer", "vejnavn": "Prøvegade", "postnr": "9999"},
        {"type": "navngivenvejpostnummer", "vejnavn": "Prøvegade", "postnr": "8888"},
        _hit("Prøvegade 1, 7777 Tredjeby", "husnummer"),
    ]}})
    assert address.street_buildings("Prøvegade") == []
    assert len(asked) == 1, "should not ask once per town"


def test_a_street_in_one_town_is_listed_in_house_order(monkeypatch):
    _stub(monkeypatch, {"husnumre/soeg": {"status": "ok", "fund": [
        _hit("Prøvegade 10, 9999 Prøveby", "husnummer"),
        _hit("Prøvegade 2, 9999 Prøveby", "husnummer"),
        _hit("Prøvegade 2A, 9999 Prøveby", "husnummer"),
        _hit("Prøvegadestræde 1, 9999 Prøveby", "husnummer"),
    ]}})
    found = address.street_buildings("Prøvegade")
    assert [b["husnummer"] for b in found] == ["2", "2A", "10"]


def test_autocomplete_drops_streets_and_keeps_unit_uuids(monkeypatch):
    _stub(monkeypatch, {"adresser/soeg": {"status": "ok", "fund": [
        {"type": "navngivenvejpostnummer", "titel": "Prøvegade 9999 Prøveby"},
        _hit("Prøvegade 1, 9999 Prøveby", "husnummer"),
        _hit("Prøvegade 1, 2., 9999 Prøveby"),
    ]}})
    found = address.autocomplete("Prøvegade 1")
    assert [(f["tekst"], f["adresse_uuid"]) for f in found] == [
        ("Prøvegade 1, 9999 Prøveby", ""),
        ("Prøvegade 1, 2., 9999 Prøveby", "id-Prøvegade 1, 2., 9999 Prøveby"),
    ]


def test_an_unreachable_register_is_an_empty_answer(monkeypatch):
    import requests

    def down(path, **params):
        raise requests.ConnectionError("down")

    monkeypatch.setattr(address, "_get", down)
    assert address.autocomplete("Prøvegade 1") == []
    assert address.street_buildings("Prøvegade", "9999") == []
    assert address.dawa_addresses(
        {"vejnavn": "Prøvegade", "husnummer": "1", "postnummer": "9999"}
    ) == {}
