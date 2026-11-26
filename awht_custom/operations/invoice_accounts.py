import frappe


def _normalize(value):
	return "".join(str(value or "").lower().replace("-", "").split())


def _branch_expense_account(company, branch):
	branch_norm = _normalize(branch)
	if not company or not branch_norm:
		return None
	accounts = frappe.get_all(
		"Account",
		filters={"company": company, "is_group": 0, "root_type": "Expense"},
		fields=["name", "account_name", "account_type"],
	)
	matches = [
		account for account in accounts
		if account.get("account_name") and branch_norm in _normalize(account.get("account_name"))
	]
	if not matches:
		return None
	matches.sort(
		key=lambda account: (
			0 if account.get("account_type") == "Cost of Goods Sold" else 1,
			len(_normalize(account.get("account_name"))),
		)
	)
	return matches[0]["name"]


def set_branch_item_expense_account(doc, method=None):
	if (doc.get("docstatus") or 0) != 0:
		return
	branch, company = doc.get("custom_branch"), doc.get("company")
	items = doc.get("items") or []
	if not branch or not company or not items:
		return

	account = _branch_expense_account(company, branch)
	if not account:
		return

	for item in items:
		item.expense_account = account
