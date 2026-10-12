"""One vocabulary for delegation ids (delegation design 2.4 row 5, SEC-L1, LAY-X3; WP1).

The delegation package owns the id format `d_<16 hex>` (`orchestrator.tools.delegation.DELEGATION_ID_RE`); the audit log
(the channel `delegation:<id>` and the field `delegation`), the installed-pack provenance (`installed_via`) and the
chat feed's references (`FeedRefs.delegation_id`, WP3) all take exactly the ids it takes.
"""

import re

import pytest
from pydantic import ValidationError

from orchestrator.Core import chat_feed
from orchestrator.Core.chat_feed import FeedRefs
from orchestrator.Core.community_manager import Provenance
from orchestrator.tools.audit import _CHANNEL, AuditLog
from orchestrator.tools.delegation import DELEGATION_ID_RE, delegation_id_ok

GOOD = ["d_0123456789abcdef", "d_" + "f" * 16]
BAD = ["d_0123456789ABCDEF", "d_123", "d_" + "0" * 17, "g_0123456789abcdef", "dg_0123456789abcdef",
       " d_0123456789abcdef", "d_0123456789abcdef\n", "", "delegation:d_0123456789abcdef"]


def test_the_feed_uses_the_delegation_packages_own_pattern():
    assert chat_feed.DELEGATION_ID_RE is DELEGATION_ID_RE


@pytest.mark.parametrize("value", GOOD + BAD)
def test_feed_refs_accept_delegation_ids(value, tmp_path):
    good = value in GOOD
    refs = FeedRefs
    if good:
        assert refs(delegation_id=value).delegation_id == value
    else:
        with pytest.raises(ValidationError):
            refs(delegation_id=value)
    # Every other consumer takes exactly the same ids.
    assert delegation_id_ok(value) is good
    assert bool(re.fullmatch(DELEGATION_ID_RE.strip("^$"), value)) is good
    assert bool(_CHANNEL.fullmatch(f"delegation:{value}")) is good
    try:
        AuditLog(tmp_path / "audit.db").record("mcp", "start_build", "local_write", "ask", "started",
                                               extra={"delegation": value})
        recorded = True
    except ValueError:
        recorded = False
    assert recorded is good or value == "", value  # an empty field is simply not recorded
    pack = dict(kind="target", id="demo", version="1.0.0", trust="verified", source="index", pack_sha256="a" * 64,
                content_sha256="b" * 64, author="someone", installed_at="2026-10-12T10:00:00")
    try:
        Provenance(**pack, installed_via=f"delegation:{value}")
        installed = True
    except ValidationError:
        installed = False
    assert installed is good
