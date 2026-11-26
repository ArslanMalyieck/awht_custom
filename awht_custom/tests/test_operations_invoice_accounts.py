import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from awht_custom.operations import invoice_accounts


ACCOUNTS = [
	{"name": "Stock Adjustment    Dammam Van - AWH", "account_name": "Stock Adjustment    Dammam Van", "account_type": "Stock Adjustment"},
	{"name": "Cost of Goods Sold   Dammam Van - AWH", "account_name": "Cost of Goods Sold   Dammam Van", "account_type": "Cost of Goods Sold"},
	{"name": "Cost of Goods Sold - AWH", "account_name": "Cost of Goods Sold", "account_type": "Cost of Goods Sold"},
	{"name": "Cost of Goods Sold   Khamis Mashit - AWH", "account_name": "Cost of Goods Sold   Khamis Mashit", "account_type": "Cost of Goods Sold"},
	{"name": "Cost of Goods Sold   Khamis Mashit Van - AWH", "account_name": "Cost of Goods Sold   Khamis Mashit Van", "account_type": "Cost of Goods Sold"},
	{"name": "Cost of Goods Sold   HeadOffice - AWH", "account_name": "Cost of Goods Sold   HeadOffice", "account_type": "Cost of Goods Sold"},
]


class TestBranchExpenseAccounts(unittest.TestCase):
	def test_prefers_cost_of_goods_sold_over_stock_adjustment_for_dammam(self):
		items = [SimpleNamespace(expense_account="Cost of Goods Sold - AWH")]
		doc = {"docstatus": 0, "custom_branch": "DAMMAM VAN", "company": "AWH", "items": items}
		with patch.object(invoice_accounts.frappe, "get_all", return_value=ACCOUNTS):
			invoice_accounts.set_branch_item_expense_account(doc)
		self.assertEqual(items[0].expense_account, "Cost of Goods Sold   Dammam Van - AWH")

	def test_uses_exact_khamis_branch_account(self):
		items = [SimpleNamespace(expense_account="old")]
		doc = {"docstatus": 0, "custom_branch": "KHAMIS MASHIT VAN", "company": "AWH", "items": items}
		with patch.object(invoice_accounts.frappe, "get_all", return_value=ACCOUNTS):
			invoice_accounts.set_branch_item_expense_account(doc)
		self.assertEqual(items[0].expense_account, "Cost of Goods Sold   Khamis Mashit Van - AWH")

	def test_prefers_shorter_parent_branch_account_for_khamis(self):
		items = [SimpleNamespace(expense_account="old")]
		doc = {"docstatus": 0, "custom_branch": "KHAMIS MASHIT", "company": "AWH", "items": items}
		with patch.object(invoice_accounts.frappe, "get_all", return_value=ACCOUNTS):
			invoice_accounts.set_branch_item_expense_account(doc)
		self.assertEqual(items[0].expense_account, "Cost of Goods Sold   Khamis Mashit - AWH")

	def test_head_office_spacing_is_normalized(self):
		items = [SimpleNamespace(expense_account="old")]
		doc = {"docstatus": 0, "custom_branch": "Head Office", "company": "AWH", "items": items}
		with patch.object(invoice_accounts.frappe, "get_all", return_value=ACCOUNTS):
			invoice_accounts.set_branch_item_expense_account(doc)
		self.assertEqual(items[0].expense_account, "Cost of Goods Sold   HeadOffice - AWH")

	def test_no_matching_account_leaves_existing_value(self):
		items = [SimpleNamespace(expense_account="existing - AWH")]
		doc = {"docstatus": 0, "custom_branch": "WAREHOUSE", "company": "AWH", "items": items}
		with patch.object(invoice_accounts.frappe, "get_all", return_value=ACCOUNTS):
			invoice_accounts.set_branch_item_expense_account(doc)
		self.assertEqual(items[0].expense_account, "existing - AWH")

	def test_submitted_invoice_is_not_modified(self):
		items = [SimpleNamespace(expense_account="existing - AWH")]
		doc = {"docstatus": 1, "custom_branch": "DAMMAM VAN", "company": "AWH", "items": items}
		get_all = MagicMock(return_value=ACCOUNTS)
		with patch.object(invoice_accounts.frappe, "get_all", get_all):
			invoice_accounts.set_branch_item_expense_account(doc)
		self.assertEqual(items[0].expense_account, "existing - AWH")
		get_all.assert_not_called()


if __name__ == "__main__":
	unittest.main()
