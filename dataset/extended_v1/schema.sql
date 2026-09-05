
PRAGMA foreign_keys=ON;
CREATE TABLE organization(organization_id TEXT PRIMARY KEY, name TEXT NOT NULL, currency TEXT CHECK(currency='INR'), provenance TEXT);
CREATE TABLE bank(bank_code TEXT PRIMARY KEY, bank_name TEXT NOT NULL);
CREATE TABLE entity(entity_id TEXT PRIMARY KEY, organization_id TEXT REFERENCES organization, scope_basis TEXT);
CREATE TABLE account(account_id TEXT PRIMARY KEY,entity_id TEXT REFERENCES entity,bank_code TEXT REFERENCES bank,account_number TEXT,program_id INTEGER,available_balance_minor INTEGER,balance_as_of TEXT);
CREATE TABLE chart_of_accounts(category_code TEXT PRIMARY KEY,category_name TEXT,account_type TEXT);
CREATE TABLE vendor(vendor_id TEXT PRIMARY KEY,vendor_name TEXT,category_code TEXT REFERENCES chart_of_accounts,provenance TEXT);
CREATE TABLE vendor_alias(alias_id TEXT PRIMARY KEY,alias TEXT,vendor_id TEXT REFERENCES vendor);
CREATE TABLE bank_transaction(transaction_id TEXT PRIMARY KEY,account_id TEXT REFERENCES account,transaction_date TEXT NOT NULL,transaction_type TEXT CHECK(transaction_type IN ('debit','credit')),description TEXT,amount_minor INTEGER NOT NULL,reference_id TEXT,utr_number TEXT,provenance TEXT,CHECK((transaction_type='debit' AND amount_minor<0) OR (transaction_type='credit' AND amount_minor>0)));
CREATE TABLE cash_allocation(allocation_id TEXT PRIMARY KEY,transaction_id TEXT REFERENCES bank_transaction,vendor_id TEXT REFERENCES vendor,category_code TEXT REFERENCES chart_of_accounts,kind TEXT CHECK(kind IN ('vendor_payment','vendor_refund','bank_fee','internal_transfer','unclassified')),amount_minor INTEGER NOT NULL,provenance TEXT);
CREATE TABLE invoice(invoice_id TEXT PRIMARY KEY,vendor_id TEXT REFERENCES vendor,invoice_date TEXT,due_date TEXT,amount_minor INTEGER CHECK(amount_minor>0),provenance TEXT);
CREATE TABLE payout(payout_id TEXT PRIMARY KEY,invoice_id TEXT REFERENCES invoice,vendor_id TEXT REFERENCES vendor,account_id TEXT REFERENCES account,requested_date TEXT,settled_date TEXT,amount_minor INTEGER CHECK(amount_minor>0),status TEXT CHECK(status IN ('succeeded','pending','processing','failed','cancelled','reversed')),provenance TEXT);
CREATE TABLE payout_attempt(attempt_id TEXT PRIMARY KEY,payout_id TEXT REFERENCES payout,attempt_number INTEGER,attempt_date TEXT,status TEXT CHECK(status IN ('succeeded','pending','processing','failed','cancelled')),failure_code TEXT,UNIQUE(payout_id,attempt_number));
CREATE TABLE payout_transaction(payout_id TEXT REFERENCES payout,transaction_id TEXT REFERENCES bank_transaction,role TEXT CHECK(role IN ('settlement','reversal','suspected_duplicate')),PRIMARY KEY(payout_id,transaction_id));
CREATE TABLE invoice_allocation(allocation_id TEXT PRIMARY KEY,invoice_id TEXT REFERENCES invoice,payout_id TEXT REFERENCES payout,amount_minor INTEGER NOT NULL,allocation_date TEXT);
CREATE TABLE refund(refund_id TEXT PRIMARY KEY,payout_id TEXT REFERENCES payout,transaction_id TEXT UNIQUE REFERENCES bank_transaction,amount_minor INTEGER CHECK(amount_minor>0),reason TEXT,reopens_invoice INTEGER CHECK(reopens_invoice IN (0,1)));
CREATE TABLE expected_entry(entry_id TEXT PRIMARY KEY,account_id TEXT REFERENCES account,payout_id TEXT REFERENCES payout,expected_date TEXT,direction TEXT CHECK(direction IN ('debit','credit')),amount_minor INTEGER CHECK(amount_minor>0),entry_kind TEXT);
CREATE TABLE reconciliation_case(case_id TEXT PRIMARY KEY,status TEXT CHECK(status IN ('reconciled','unreconciled','discrepancy','ambiguous','not_due')),reason_code TEXT,as_of_date TEXT,review_note TEXT);
CREATE TABLE case_expected(case_id TEXT REFERENCES reconciliation_case,entry_id TEXT UNIQUE REFERENCES expected_entry,PRIMARY KEY(case_id,entry_id));
CREATE TABLE case_observed(case_id TEXT REFERENCES reconciliation_case,transaction_id TEXT UNIQUE REFERENCES bank_transaction,PRIMARY KEY(case_id,transaction_id));
CREATE TABLE reconciliation_match(match_id TEXT PRIMARY KEY,case_id TEXT REFERENCES reconciliation_case,entry_id TEXT REFERENCES expected_entry,transaction_id TEXT REFERENCES bank_transaction,amount_minor INTEGER CHECK(amount_minor>0));
CREATE TABLE internal_transfer(transfer_id TEXT PRIMARY KEY,debit_transaction_id TEXT UNIQUE REFERENCES bank_transaction,credit_transaction_id TEXT UNIQUE REFERENCES bank_transaction,amount_minor INTEGER CHECK(amount_minor>0));
CREATE TABLE anomaly_label(label_id TEXT PRIMARY KEY,payout_id TEXT REFERENCES payout,label TEXT,rule TEXT,baseline_payout_ids TEXT);
CREATE TABLE scenario(scenario_id TEXT PRIMARY KEY,title TEXT,record_ids_json TEXT,expected_behavior TEXT);
CREATE INDEX tx_date_type ON bank_transaction(transaction_date,transaction_type);
CREATE INDEX tx_account_date ON bank_transaction(account_id,transaction_date);
CREATE INDEX tx_reference ON bank_transaction(reference_id);
CREATE INDEX payout_vendor_date ON payout(vendor_id,settled_date);
CREATE INDEX cash_tx ON cash_allocation(transaction_id);
CREATE INDEX cash_vendor ON cash_allocation(vendor_id,kind);
CREATE VIEW v_cashflow AS SELECT t.transaction_id,t.account_id,t.transaction_date,t.reference_id,
 a.vendor_id,v.vendor_name,a.category_code,c.category_name,a.kind,a.amount_minor,
 CASE WHEN a.amount_minor<0 THEN -a.amount_minor ELSE 0 END outflow_minor,
 CASE WHEN a.amount_minor>0 THEN a.amount_minor ELSE 0 END inflow_minor
 FROM bank_transaction t JOIN cash_allocation a USING(transaction_id)
 LEFT JOIN vendor v USING(vendor_id) JOIN chart_of_accounts c USING(category_code);
CREATE VIEW v_invoice_balance AS SELECT i.*,COALESCE((SELECT SUM(a.amount_minor) FROM invoice_allocation a WHERE a.invoice_id=i.invoice_id),0) paid_minor,
 i.amount_minor-COALESCE((SELECT SUM(a.amount_minor) FROM invoice_allocation a WHERE a.invoice_id=i.invoice_id),0) outstanding_minor FROM invoice i;
CREATE VIEW v_reconciliation AS SELECT c.*,
 COALESCE((SELECT SUM(e.amount_minor) FROM case_expected x JOIN expected_entry e USING(entry_id) WHERE x.case_id=c.case_id),0) expected_minor,
 COALESCE((SELECT SUM(ABS(t.amount_minor)) FROM case_observed x JOIN bank_transaction t USING(transaction_id) WHERE x.case_id=c.case_id),0) observed_minor,
 COALESCE((SELECT SUM(m.amount_minor) FROM reconciliation_match m WHERE m.case_id=c.case_id),0) matched_minor FROM reconciliation_case c;
CREATE VIEW v_transaction_reconciliation AS SELECT t.transaction_id,COALESCE(c.status,'not_assessed') reconciliation_status,c.case_id,c.reason_code
 FROM bank_transaction t LEFT JOIN case_observed o USING(transaction_id) LEFT JOIN reconciliation_case c USING(case_id);
