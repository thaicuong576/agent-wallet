"""Cryptographic hash chain verification and JSON Lines export/import for audit log."""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, TextIO

from core.gateway.audit import AuditSink, canonical, record_hash

GENESIS_PREV_HASH = "0" * 64


@dataclass
class VerifyReport:
    """Report produced by verify_chain."""

    valid: bool
    count: int
    bad_seq: int | None = None
    # Tamper kind: "modified_field", "deleted_record", "reordered_record", "broken_prev_hash"
    tamper_kind: str | None = None
    error: str | None = None
    details: dict[str, Any] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        """Alias for valid."""
        return self.valid

    def __bool__(self) -> bool:
        return self.valid


def _extract_records(records_or_sink: Any) -> list[dict[str, Any]]:
    """Extract list of record dictionaries from a sink, iterable, or file path."""
    if isinstance(records_or_sink, (str, Path)):
        path = Path(records_or_sink)
        if not path.exists():
            raise FileNotFoundError(f"File not found: {path}")

        # Check if SQLite DB via magic header
        is_sqlite = False
        if path.is_file():
            with open(path, "rb") as f:
                header = f.read(16)
                if header.startswith(b"SQLite format 3\000"):
                    is_sqlite = True

        if is_sqlite or path.suffix.lower() in (".db", ".sqlite", ".sqlite3"):
            from core.audit.sink import SqliteAuditSink

            sink = SqliteAuditSink(path)
            try:
                return sink.records
            finally:
                sink.close()
        else:
            return import_jsonl(path)

    if hasattr(records_or_sink, "records"):
        recs = records_or_sink.records
        if callable(recs):
            return list(recs())
        return list(recs)

    if isinstance(records_or_sink, (list, tuple)):
        return list(records_or_sink)

    if isinstance(records_or_sink, Iterable):
        return list(records_or_sink)

    raise TypeError(f"Unsupported records_or_sink type: {type(records_or_sink)}")


def verify_chain(records_or_sink: Any) -> VerifyReport:
    """Verify cryptographic integrity of an audit log.

    Detects:
      - modified field: record hash does not match computed record_hash
      - deleted record: sequence numbers contain a gap or missing genesis
      - reordered record: sequence numbers appear out of order
      - broken prev_hash: prev_hash does not match previous record's hash

    Returns VerifyReport naming the first bad sequence number.
    """
    records = _extract_records(records_or_sink)

    if not records:
        return VerifyReport(valid=True, count=0)

    # Pre-index present seq numbers to distinguish deleted vs reordered records
    all_seqs: set[int] = set()
    for r in records:
        if isinstance(r, dict) and isinstance(r.get("seq"), int):
            all_seqs.add(r["seq"])

    prev = GENESIS_PREV_HASH

    for i, rec in enumerate(records):
        if not isinstance(rec, dict):
            return VerifyReport(
                valid=False,
                count=i,
                bad_seq=i,
                tamper_kind="invalid_record",
                error=f"Record at index {i} is not a dictionary: {type(rec)}",
            )

        seq = rec.get("seq")

        # 1. Check sequence monotonicity and gaps
        if seq != i:
            if i not in all_seqs:
                # Sequence i was expected here but was omitted entirely
                return VerifyReport(
                    valid=False,
                    count=i,
                    bad_seq=i,
                    tamper_kind="deleted_record",
                    error=f"Missing record at sequence {i} (found seq {seq} instead)",
                    details={"expected_seq": i, "actual_seq": seq},
                )
            else:
                # Sequence i exists elsewhere in the log, so records are shuffled
                return VerifyReport(
                    valid=False,
                    count=i,
                    bad_seq=i,
                    tamper_kind="reordered_record",
                    error=f"Reordered records at index {i} (expected seq {i}, got {seq})",
                    details={"expected_seq": i, "actual_seq": seq},
                )

        # 2. Check prev_hash cryptographic linkage
        rec_prev = rec.get("prev_hash")
        if rec_prev != prev:
            return VerifyReport(
                valid=False,
                count=i,
                bad_seq=i,
                tamper_kind="broken_prev_hash",
                error=f"Broken prev_hash at seq {i}: expected {prev}, got {rec_prev}",
                details={"expected_prev_hash": prev, "actual_prev_hash": rec_prev},
            )

        # 3. Check record content hash
        rec_hash = rec.get("hash")
        computed_hash = record_hash(rec)
        if rec_hash != computed_hash:
            return VerifyReport(
                valid=False,
                count=i,
                bad_seq=i,
                tamper_kind="modified_field",
                error=(
                    f"Modified field at seq {i}: hash mismatch "
                    f"(stored {rec_hash}, computed {computed_hash})"
                ),
                details={"stored_hash": rec_hash, "computed_hash": computed_hash},
            )

        prev = rec_hash

    return VerifyReport(valid=True, count=len(records))


def export_jsonl(records_or_sink: Any, target: str | Path | TextIO) -> int:
    """Export audit records to JSON Lines format (canonical JSON per line).

    Returns count of exported records.
    """
    records = _extract_records(records_or_sink)

    def _write_records(out_f: TextIO) -> int:
        count = 0
        for r in records:
            line = canonical(r).decode("utf-8")
            out_f.write(line + "\n")
            count += 1
        return count

    if hasattr(target, "write"):
        return _write_records(target)

    target_path = Path(target)
    target_path.parent.mkdir(parents=True, exist_ok=True)
    with open(target_path, "w", encoding="utf-8") as f:
        return _write_records(f)


def import_jsonl(
    source: str | Path | TextIO,
    target_sink: AuditSink | None = None,
) -> list[dict[str, Any]]:
    """Import audit records from a JSON Lines stream or file.

    Optionally imports records into target_sink. Returns list of records.
    """

    def _read_records(in_f: TextIO) -> list[dict[str, Any]]:
        records = []
        for line in in_f:
            stripped = line.strip()
            if not stripped:
                continue
            records.append(json.loads(stripped))
        return records

    if hasattr(source, "read"):
        records = _read_records(source)
    else:
        source_path = Path(source)
        with open(source_path, encoding="utf-8") as f:
            records = _read_records(f)

    if target_sink is not None:
        if hasattr(target_sink, "import_records"):
            target_sink.import_records(records)
        else:
            for r in records:
                target_sink.append(r)

    return records


def main(argv: list[str] | None = None) -> int:
    """CLI entrypoint for verifying audit log integrity."""
    parser = argparse.ArgumentParser(
        prog="python -m core.audit.verify",
        description="Verify cryptographic integrity of an audit log (SQLite DB or JSONL).",
    )
    parser.add_argument(
        "path",
        help="Path to the SQLite database file (.db) or JSON Lines file (.jsonl)",
    )
    args = parser.parse_args(argv)

    target_path = Path(args.path)
    if not target_path.exists():
        sys.stderr.write(f"Error: file not found: {target_path}\n")
        return 2

    try:
        report = verify_chain(target_path)
    except Exception as exc:
        sys.stderr.write(f"Error reading audit log: {exc}\n")
        return 2

    if report.valid:
        sys.stdout.write(
            f"OK: verified {report.count} records in {target_path} (hash chain intact)\n"
        )
        return 0
    else:
        sys.stderr.write(
            f"TAMPER DETECTED: {report.error} "
            f"(bad_seq={report.bad_seq}, kind={report.tamper_kind})\n"
        )
        return 1


if __name__ == "__main__":
    sys.exit(main())
