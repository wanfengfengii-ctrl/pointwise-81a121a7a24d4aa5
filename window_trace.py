import argparse
from dataclasses import replace
from fractions import Fraction
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase

from capture.evidence import EvidenceMismatch
from capture.intake import CaptureCommitFailed
from delivery.continuity import ContinuityGap, DeliverySegment
from recorder.pages import PhysicalPage, RawSlot, SlotRef
from recorder.status import PageUnavailable
from review.workbench import initialize_workspace, open_workbench


def parse_fixture(data):
    page_id = data["page"]
    slots = tuple(RawSlot(SlotRef(page_id, item["coordinate"]), item["position"],
                          item["coordinate"], item["raw"].encode("ascii"))
                  for item in data["slots"])
    page = PhysicalPage(page_id, data["line"], slots)
    page.header()
    windows = {item["coordinate"]: set(item["beats"]) for item in data["slots"]}
    refs = {item["ticket"]: slot.ref for item, slot in zip(data["slots"], slots)}
    return page, windows, refs


def check_capacity(desk, check, *, expired):
    page_id = "elapsed" if expired else "collision"
    desk.lines.register(page_id, 1, 1)
    refs = [SlotRef(page_id, name) for name in ("A", "B")]
    page = PhysicalPage(page_id, page_id, (RawSlot(refs[0], 1, "A", b"7"),
                                           RawSlot(refs[1], 2, "B", b"11")))
    desk.feed.feed_windowed(page, {"A": {1}, "B": {1}})
    first, second = page_id + "-a", page_id + "-b"
    check.assertEqual(desk.feed.book(first, refs[0]), "accepted")
    if expired:
        check.assertEqual(desk.feed.tick(page_id, 1), first)
    check.assertEqual(desk.feed.book(second, refs[1]), "unavailable")
    check.assertEqual(desk.feed.tick(page_id, 1), first)
    check.assertEqual(desk.intake.collect(second), "pending")


def run_trace(data):
    check = TestCase()
    page, windows, refs = parse_fixture(data)
    first, second = data["booking_order"]
    segment = DeliverySegment(2, (page.page_id,))
    with TemporaryDirectory(prefix="read-window-") as directory:
        path = Path(directory) / "work.sqlite"
        initialize_workspace(path)
        with open_workbench(path) as desk:
            desk.lines.register(page.line, *data["ratio"])
            desk.feed.feed_windowed(page, windows)
            for ticket in data["booking_order"]:
                check.assertEqual(desk.feed.book(ticket, refs[ticket]), "accepted")
            check.assertIsNone(desk.feed.read_terminal(first))
            check.assertEqual(desk.intake.collect(first), "pending")
            with check.assertRaises(PageUnavailable):
                desk.feed.read_slot(refs[first])
            with check.assertRaises(PageUnavailable):
                desk.feed.read_page(page.page_id)
            with check.assertRaises(PageUnavailable):
                desk.intake.accept(page.page_id)
            with check.assertRaises(ValueError):
                desk.feed.tick(page.page_id, 2)
        with open_workbench(path) as desk:
            picks = [desk.feed.tick(page.page_id, beat) for beat in data["ticks"]]
            check.assertEqual(picks, [second, first])
        with open_workbench(path) as desk:
            check.assertEqual(desk.feed.tick(page.page_id, 1), second)
            check.assertEqual(desk.feed.book(first, refs[first]), "accepted")
            read_a = desk.feed.read_terminal(first)
            read_b = desk.feed.read_terminal(second)
            check.assertEqual((read_a.beat, read_b.beat), (2, 1))
            with check.assertRaises(EvidenceMismatch):
                desk.intake.collect(first, terminal=replace(read_b, ticket=first))
            with check.assertRaises(EvidenceMismatch):
                desk.intake.collect(first, terminal=replace(read_a, beat=1))
            check.assertIsNone(desk.intake.lookup_read(first))
            check.assertEqual(desk.intake.collect(first, terminal=read_a), read_a)
            desk.intake.fail_next.add(page.page_id)
            with check.assertRaises(CaptureCommitFailed):
                desk.intake.collect(second, terminal=read_b)
            check.assertIsNone(desk.intake.lookup_read(second))
            check.assertIsNone(desk.depot.get_slot(refs[second]))
            with check.assertRaises(ContinuityGap):
                desk.publisher.publish(page.line, segment)
        with open_workbench(path) as desk:
            check.assertEqual(desk.intake.collect(second, terminal=read_b), read_b)
            check_capacity(desk, check, expired=False)
            check_capacity(desk, check, expired=True)
        with open_workbench(path) as desk:
            desk.device.stop()
            arrived = [desk.intake.lookup_read(ticket) for ticket in (first, second)]
            check.assertEqual(arrived, [read_a, read_b])
            check.assertEqual([entry.slot for entry in arrived], list(page.slots))
            check.assertEqual(desk.intake.collect(second), read_b)
            result = desk.publisher.publish(page.line, segment)
            saved = desk.lookup.review(result.result_id)
            check.assertEqual([row.value for row in saved.readings],
                              [Fraction(21, 2), Fraction(33, 2)])
            check.assertEqual([row.source for row in saved.readings], list(refs.values()))
            print(json.dumps({"page": page.page_id, "device_order": picks,
                              "reads": [{"ticket": row.ticket, "slot": row.slot.ref.coordinate,
                                         "beat": row.beat, "raw": row.slot.payload.decode("ascii")}
                                        for row in arrived],
                              "published": [str(row.value) for row in saved.readings],
                              "receipt": saved.receipt.receipt_id}, ensure_ascii=False))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("mode", choices=("fixture", "trace"))
    args = parser.parse_args()
    with (Path(__file__).parent / "samples" / "read_windows.json").open(encoding="utf-8") as handle:
        data = json.load(handle)
    if args.mode == "fixture":
        page, windows, refs = parse_fixture(data)
        print(page.page_id, windows, refs)
    else:
        run_trace(data)


if __name__ == "__main__":
    main()
