import unittest

from awht_custom.stock_recon import api


class TestStockReconHelpers(unittest.TestCase):
    def test_item_key_normalizes_missing_batch_and_warehouse(self):
        self.assertEqual(api._item_key({"item_code": "A"}), ("A", "", ""))
        self.assertEqual(
            api._item_key({"item_code": "A", "warehouse": "W", "batch_no": "B"}),
            ("A", "W", "B"),
        )

    def test_remaining_rows_excludes_item_warehouse_batch(self):
        rows = [
            {"item_code": "A", "warehouse": "W", "batch_no": None},
            {"item_code": "B", "warehouse": "W", "batch_no": None},
            {"item_code": "A", "warehouse": "W", "batch_no": "LOT-1"},
            {"item_code": "A", "warehouse": "OTHER", "batch_no": None},
        ]
        reconciled = {("A", "W", ""), ("B", "W", "")}
        self.assertEqual(
            [(row["item_code"], row["warehouse"], row["batch_no"]) for row in api._remaining_rows(rows, reconciled)],
            [("A", "W", "LOT-1"), ("A", "OTHER", None)],
        )

    def test_remaining_rows_keeps_duplicate_batches_when_no_batch_reconciled(self):
        rows = [
            {"item_code": "A", "warehouse": "W", "batch_no": "LOT-1"},
            {"item_code": "A", "warehouse": "W", "batch_no": "LOT-2"},
        ]
        self.assertEqual(len(api._remaining_rows(rows, set())), 2)


if __name__ == "__main__":
    unittest.main()
