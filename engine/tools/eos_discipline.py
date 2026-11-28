"""Shared activation policy. Host/provider selection is outside this module."""
from pathlib import Path

SKILL = "fable-5-1"
REVISION = 1
SOURCE = Path("skills") / SKILL / "core" / "activation-discipline.md"


def describe(engine):
    try:
        path = Path(engine) / SOURCE
        with path.open(encoding="utf-8") as stream:
            text = stream.read(1601).strip()
        if not text or len(text) > 1600:
            raise ValueError("discipline length is outside the supported bound")
    except (OSError, ValueError):
        return {"status": "UNAVAILABLE", "source": SOURCE.as_posix()}
    return {"status": "AVAILABLE", "revision": REVISION, "source": SOURCE.as_posix(), "text": text}


def render(policy):
    if policy.get("status") == "AVAILABLE":
        return f'<eos-discipline revision="{policy["revision"]}">\n{policy["text"]}\n</eos-discipline>'
    return "<eos-discipline>Shared discipline is unavailable. Apply the existing EOS rules and report the missing source.</eos-discipline>"
