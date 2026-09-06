"""The interface in two languages, and the data in neither.

Run directly - `uv run python tests/test_i18n.py` - or under pytest.

The catalogue itself is checked mechanically here rather than by eye. A
translation that quietly loses a placeholder raises at format time, on a
screen, in front of somebody; the test below finds it while the file is open.
"""

import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from yaybo import i18n


def test_english_is_the_source_and_needs_no_lookup():
    """The string in the code is the English one, so the default language
    cannot have a missing translation."""
    i18n.use("en")
    assert i18n.t("Address") == "Address"
    assert i18n.t("nothing anybody has translated") == "nothing anybody has translated"


def test_danish_comes_out_of_the_catalogue():
    i18n.use("da")
    assert i18n.t("Address") == "Adresse"
    assert i18n.t("Charges") == "Hæftelser"
    i18n.use("en")


def test_an_untranslated_string_degrades_into_english_not_into_a_key():
    """The whole reason English is the source: a gap in the catalogue reads as
    a slightly foreign interface rather than as `library.column.address`."""
    i18n.use("da")
    assert i18n.t("A sentence nobody has translated yet.") == (
        "A sentence nobody has translated yet."
    )
    i18n.use("en")


def test_every_translation_keeps_the_placeholders_its_english_has():
    """A translation that drops {name} raises KeyError the moment the screen
    draws it, which is both too late and somewhere else."""
    for english, danish in i18n.DANISH.items():
        wanted = set(re.findall(r"{(\w+)", english))
        given = set(re.findall(r"{(\w+)", danish))
        assert wanted == given, f"{english!r} -> {danish!r}"


def test_formatting_happens_after_translation():
    """So a translation may put the values in a different order from the
    English, which Danish regularly wants."""
    i18n.use("da")
    assert i18n.t("{n} property", n=1) == "1 ejendom"
    assert i18n.t("{n} properties", n=4) == "4 ejendomme"
    i18n.use("en")
    assert i18n.t("{n} properties", n=4) == "4 properties"


def test_an_unknown_language_falls_back_rather_than_raising():
    """This is read from a settings file a person can edit."""
    assert i18n.use("kl") == i18n.DEFAULT
    assert i18n.use("da") == "da"
    i18n.use("en")


def test_the_choice_is_remembered(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    assert i18n.load() == "en", "English until somebody says otherwise"
    i18n.save("da")
    assert json.loads(i18n.settings_path().read_text())["language"] == "da"
    assert i18n.load() == "da"
    i18n.use("en")


def test_remembering_the_language_keeps_the_rest_of_the_settings(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    i18n.settings_path().parent.mkdir(parents=True, exist_ok=True)
    i18n.settings_path().write_text(json.dumps({"something": "else"}))
    i18n.save("da")
    held = json.loads(i18n.settings_path().read_text())
    assert held == {"something": "else", "language": "da"}
    i18n.use("en")


def test_an_unreadable_settings_file_does_not_stop_it_starting(tmp_path, monkeypatch):
    """This runs before there is a screen to complain on."""
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    i18n.settings_path().parent.mkdir(parents=True, exist_ok=True)
    i18n.settings_path().write_text("{not json at all")
    assert i18n.load() == "en"


def test_bindings_are_restated_on_the_instance():
    """Textual reads BINDINGS off the class, on import, before anybody has
    chosen a language - so the footer has to be rewritten per instance.

    This asserts the private attribute it reaches into still exists. An
    upgrade that moves it should fail here rather than quietly reverting the
    footer to English.
    """
    from textual.binding import Binding, BindingsMap

    class Node:
        # Declared so the type checker knows what translate_bindings reaches
        # for; the real thing is a Screen, which Textual gives this to.
        _bindings: BindingsMap

    node = Node()
    node._bindings = BindingsMap([Binding("f", "refetch", "Re-fetch")])

    i18n.use("da")
    i18n.translate_bindings(node)
    said = [b.description for group in node._bindings.key_to_bindings.values()
            for b in group]
    assert said == ["Hent igen"]
    i18n.use("en")


def test_a_binding_with_no_description_is_left_alone():
    from textual.binding import Binding, BindingsMap

    class Node:
        _bindings: BindingsMap

    node = Node()
    node._bindings = BindingsMap([Binding("f", "refetch", "")])
    i18n.use("da")
    i18n.translate_bindings(node)
    assert [b.description for group in node._bindings.key_to_bindings.values()
            for b in group] == [""]
    i18n.use("en")


def test_translating_something_that_has_no_bindings_does_nothing():
    class Node:
        pass

    i18n.translate_bindings(Node())  # must not raise


# Words that are the same in both languages. An entry mapping a string to
# itself would be a line to keep in step with nothing, so they are listed here
# instead and the check below skips them.
SAME_IN_BOTH = {"Filter", "SQL", "MitID", "Boligsiden", "Median", "Start",
                "Type", "Postnr", "Note", "Pri.", "▶  Start", "■  Stop",
                "Auto-fetch: on", "Auto-fetch: off", "Stop"}


def test_every_footer_key_has_a_danish_word():
    """The footer is the one place a missing translation is unmissable: it
    sits under every screen, in both languages, all the time.

    Read out of the source rather than off a running application, because a
    binding on a screen nobody opened during the tests would otherwise never
    be checked.
    """
    said = set()
    for path in (Path(__file__).resolve().parent.parent / "src/yaybo").rglob("*.py"):
        for found in re.finditer(
            r'Binding\(\s*"[^"]+",\s*"[^"]+",\s*"([^"]+)"', path.read_text()
        ):
            said.add(found.group(1))
    assert said, "found no bindings at all - has the pattern changed?"
    missing = sorted(d for d in said if d not in i18n.DANISH and d not in SAME_IN_BOTH)
    assert not missing, f"no Danish for: {missing}"


def test_the_register_is_never_translated():
    """The point of the whole module. A value out of the register is Danish
    and stays Danish, however the buttons around it are labelled."""
    i18n.use("en")
    from yaybo.register import rows as build

    charges = build.andel_haeftelse_rows(
        {
            "adresse": "Prøvegade 1, ST. TH, 9999 Prøveby",
            "haeftelser": [{"alias": "01.02.2024-1", "version": "1",
                            "haeftelsestype": "Ejerpantebrev",
                            "hovedstol": "1.000.000 DKK", "kreditorer": []}],
        },
        "andel-1",
    )
    assert charges[0]["dokumenttype"] == "Ejerpantebrev"
    assert charges[0]["adresse"].endswith("Prøveby")


if __name__ == "__main__":
    import tempfile

    class _Patch:
        def setenv(self, key, value):
            import os

            os.environ[key] = value

    tests = [v for n, v in sorted(globals().items()) if n.startswith("test_")]
    for test in tests:
        if test.__code__.co_argcount:
            with tempfile.TemporaryDirectory() as folder:
                test(Path(folder), _Patch())
        else:
            test()
        print(f"  ok  {test.__name__}")
    print(f"{len(tests)} passed")
