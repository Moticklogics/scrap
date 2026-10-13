import asyncio
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
FIX = Path(__file__).resolve().parent / "fixtures"
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(FIX))

from server import FixtureServer  # noqa: E402

SITES = FIX / "sites"
ROUTES = json.loads((SITES / "routes.json").read_text(encoding="utf-8"))
FAKE_BASE = "http://forum.test"

LISTING_START = {
    "xenforo2": "/xenforo2/index.php?forums/recovery.2/",
    "xenforo1": "/xenforo1/index.php?forums/general.3/",
    "phpbb": "/phpbb/viewforum.php?f=2",
    "vbulletin": "/vbulletin/forumdisplay.php?f=5",
    "discourse": "/discourse/c/general/5",
    "nodebb": "/nodebb/category/2/general-discussion",
    "flarum": "/flarum/t/general",
    "vanilla": "/vanilla/categories/general",
    "oldboard": "/oldboard/forum.php?cat=3",
    "community": "/community/",
    "spa": "/spa/board?p=1",
}
PLATFORM_OF = {"xenforo2": "xenforo2", "xenforo1": "xenforo1", "phpbb": "phpbb", "vbulletin": "vbulletin",
               "discourse": "discourse", "nodebb": "nodebb", "flarum": "flarum", "vanilla": "vanilla",
               "oldboard": None, "community": None, "spa": None}
STATIC_SITES = [s for s in LISTING_START if s != "spa"]  # spa needs JavaScript


def fixture_html(route: str) -> str:
    return (SITES / ROUTES[route]["file"]).read_text(encoding="utf-8")


def routes_for(site: str, kind: str) -> list[str]:
    """kind: 'listing' or 'thread'"""
    out = []
    for r, info in ROUTES.items():
        if not r.startswith(f"/{site}/"):
            continue
        f = info["file"]
        if f.startswith("misc/"):
            continue
        is_listing = any(k in f for k in ("forum_p", "category_p", "tag_p", "cat_p", "list_p", "board_p"))
        if (kind == "listing") == is_listing:
            out.append(r)
    return sorted(out)


def thread_id_of(route: str) -> int:
    f = ROUTES[route]["file"].rsplit("/", 1)[-1]
    digits = "".join(c for c in f.split("_")[-1] if c.isdigit())
    return int(digits)


@pytest.fixture(scope="session")
def server():
    s = FixtureServer().start()
    yield s
    s.stop()


@pytest.fixture
def fresh_server(server):
    server.reset()
    return server


def run(coro):
    return asyncio.run(coro)
