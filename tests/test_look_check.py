"""The host's look check of a new product: its DESIGN.md is the one given, every hex colour its screen code sets is one
of DESIGN.md's, and no text is coloured with the accent's fill role."""
import os

import pytest
from justshipit.contracts.look import Look
from justshipit.engineering import look_check
from justshipit.engineering.style_packs import design_md, roles

LOOK = Look(pack="toss", main="#ea580c")
DESIGN = design_md(LOOK)
ACCENT = roles(LOOK)


def tree(tmp_path, files: dict[str, str], design: str = DESIGN):
    for rel, text in {"DESIGN.md": design, **files}.items():
        (tmp_path / rel).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / rel).write_text(text, encoding="utf-8", newline="")
    return tmp_path


def _link(link, target) -> None:
    try:
        os.symlink(target, link, target_is_directory=target.is_dir())
    except OSError:
        pytest.skip("this machine cannot make symbolic links")


def test_a_product_built_from_its_tokens_passes(tmp_path):
    root = tree(tmp_path, {
        "src/tokens.css": f":root {{ --primary: {ACCENT.primary}; --primary-text: {ACCENT.primary_text}; "
                          f"--surface: #FFF; --scrim: #191F287a; }}\n",
        "src/app.css": ".link { color: var(--primary-text); }\n.btn { background: var(--primary); "
                       "color: var(--on-primary); border-color: var(--primary); }\n",
        "src/App.tsx": "export const App = () => <p>it&#039;s done</p>;\n",
        "index.html": "<div id=\"root\"></div>\n",
    })

    assert look_check.check(root, DESIGN) == look_check.Result(files=4, found=[])


def test_a_colour_outside_design_md_is_named_with_its_file_and_line(tmp_path):
    root = tree(tmp_path, {"src/app.css": f".a {{ color: {ACCENT.primary_text}; }}\n.b {{ fill: #3182F6; }}\n",
                           "index.html": "<meta name=\"theme-color\" content=\"#123456\">\n"})

    assert look_check.check(root, DESIGN).found == [
        "index.html:1: #123456 is not a colour in DESIGN.md; use its token.",
        "src/app.css:2: #3182f6 is not a colour in DESIGN.md; use its token."]


def test_a_colour_counts_in_a_style_object_a_style_attribute_and_an_svg_paint(tmp_path):
    root = tree(tmp_path, {
        "src/Card.tsx": "const s = { backgroundColor: '#010101', label: 'Order #1042' };\n"
                        "<p style=\"border: 1px solid #020202\">x</p>\n"
                        "<svg><path fill=\"#030303\" stroke={'#040404'} /><stop stopColor=\"#050505\" /></svg>\n",
    })

    assert look_check.check(root, DESIGN).found == [
        "src/Card.tsx:1: #010101 is not a colour in DESIGN.md; use its token.",
        "src/Card.tsx:2: #020202 is not a colour in DESIGN.md; use its token.",
        "src/Card.tsx:3: #030303, #040404, #050505 is not a colour in DESIGN.md; use its token."]


def test_a_hash_that_is_no_colour_is_not_counted(tmp_path):
    root = tree(tmp_path, {
        "src/Orders.tsx": "<p>Order #1042</p>\n<a href=\"#add\">Add</a>\nconst title = 'Order #1042';\n"
                          "document.querySelector('#face');\nconst u = '/orders#bad';\n",
        "src/app.css": "#bad { color: var(--primary-text); }\n.border:hover #face { outline: none; }\n"
                       ".g { fill: url(#bad); background: url(\"/a.svg#face\"); }\n",
        "index.html": "<a href=\"#add\">#1042</a>\n",
    })

    assert look_check.check(root, DESIGN).found == []


def test_text_coloured_with_the_fill_role_is_found_in_css_and_in_a_style_object(tmp_path):
    root = tree(tmp_path, {"src/app.css": ".tab { color: var(--primary); }\n",
                           "src/Tab.tsx": "const s = { color: 'var(--color-primary)' };\n"})

    assert look_check.check(root, DESIGN).found == [
        "src/Tab.tsx:1: text coloured with the fill role --color-primary; text takes primary-text.",
        "src/app.css:1: text coloured with the fill role --primary; text takes primary-text."]


def test_a_rewritten_missing_or_folder_design_md_is_a_finding_and_a_line_ending_change_is_not(tmp_path):
    assert look_check.check(tree(tmp_path / "crlf", {}, DESIGN.replace("\n", "\r\n")), DESIGN).found == []

    rewritten = tree(tmp_path / "edited", {}, DESIGN.replace(ACCENT.primary, "#000000"))
    assert look_check.check(rewritten, DESIGN).found[0].startswith("DESIGN.md is not the one this build was given")

    gone = tree(tmp_path / "gone", {})
    (gone / "DESIGN.md").unlink()
    assert look_check.check(gone, DESIGN).found[0].startswith("DESIGN.md is not the one this build was given")

    folder = tree(tmp_path / "folder", {})
    (folder / "DESIGN.md").unlink()
    (folder / "DESIGN.md").mkdir()
    assert look_check.check(folder, DESIGN).found[0].startswith("DESIGN.md is a link or a folder")


def test_a_linked_design_md_is_a_finding_and_is_never_read(tmp_path):
    root = tree(tmp_path / "p", {})
    (root / "DESIGN.md").unlink()
    (tmp_path / "copy.md").write_text(DESIGN, encoding="utf-8", newline="")
    _link(root / "DESIGN.md", tmp_path / "copy.md")

    assert look_check.check(root, DESIGN).found[0].startswith("DESIGN.md is a link or a folder")


def test_only_the_product_screen_code_is_read(tmp_path):
    root = tree(tmp_path, {"src/node_modules/lib/x.css": "a { color: #010101; }\n",
                           "src/vendor/x.css": "a { color: #010101; }\n",
                           "src/__tests__/a.tsx": "const s = { color: '#010101' };\n",
                           "src/App.test.tsx": "const s = { color: '#010101' };\n",
                           "scripts/shot.js": "const bg = { color: '#020202' };\n",
                           "src/notes.md": "color: #030303\n",
                           "src/ok.ts": "export const ok = 1;\n"})

    assert look_check.check(root, DESIGN) == look_check.Result(files=1, found=[])


def test_a_server_rendered_product_is_read_from_its_usual_roots(tmp_path):
    root = tree(tmp_path, {"templates/base.html": "<body style=\"background: #010101\"></body>\n",
                           "static/app.css": "a { color: #020202; }\n",
                           "app/page.tsx": "const s = { color: '#030303' };\n",
                           "public/site.css": "a { color: #040404; }\n"})

    assert [f.split(":")[0] for f in look_check.check(root, DESIGN).found] == [
        "app/page.tsx", "templates/base.html", "static/app.css", "public/site.css"]


def test_a_product_with_no_screen_code_reads_zero_files(tmp_path):
    assert look_check.check(tree(tmp_path, {"main.py": "print('#010101')\n"}), DESIGN) == look_check.Result(0, [])


def test_a_linked_index_html_or_src_is_never_followed(tmp_path):
    outside = tree(tmp_path / "outside", {"index.html": "<p style=\"color: #010101\"></p>\n",
                                          "src/app.css": "a { color: #020202; }\n"})
    root = tree(tmp_path / "p", {})
    _link(root / "index.html", outside / "index.html")
    _link(root / "src", outside / "src")

    assert look_check.check(root, DESIGN) == look_check.Result(0, [])


def test_a_linked_file_inside_the_screen_code_is_never_followed(tmp_path):
    outside = tree(tmp_path / "outside", {"secret.css": "a { color: #030303; }\n"})
    root = tree(tmp_path / "p", {"src/app.css": f"a {{ color: {ACCENT.primary}; }}\n"})
    (root / "src" / "nested").mkdir()
    _link(root / "src" / "theme.css", outside / "secret.css")
    _link(root / "src" / "nested" / "deep.css", outside / "secret.css")

    assert look_check.check(root, DESIGN) == look_check.Result(1, [])
