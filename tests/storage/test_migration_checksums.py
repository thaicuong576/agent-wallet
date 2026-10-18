"""Applied migrations must never change: the hosted database stores their checksums."""

from core.storage.migrate import load_migrations

APPLIED = {
    1: "1858418c8b9820f1795595ca2d6ef5ac9aece953cb98832acbd6cedcdcb28cd9",
    2: "7e9665702ca92569dcb3be983593670c3c88283875ae598d7e3757633a79799b",
    3: "f5b2c904ae37273345eef0f7a1877d1bba7cb8b2143a3ba122ed4d3e315c3a29",
    4: "caeb3e3340bb60b1b4f857fcb633468638cf7c034ebfe393f7eb66a4f49521e6",
    5: "773e1b91f4236a4fe0fd9cea7d9e7820ea91762e0b076977ddb6aa0a61ccb0a1",
    6: "44b50c285a4ceb9bda3ce1feda2ea26ac288545a584cc5a33aed986a0c7e1937",
}


def test_applied_migrations_are_unchanged():
    found = {m.version: m.checksum for m in load_migrations()}
    for version, checksum in APPLIED.items():
        assert found.get(version) == checksum, (
            f"migration {version} was edited after it was applied; add a new migration instead"
        )
