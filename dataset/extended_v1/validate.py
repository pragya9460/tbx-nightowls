"""Validate the packaged dataset without third-party dependencies or network access."""
import csv
import hashlib
import json
import sqlite3
from decimal import Decimal
from pathlib import Path

root=Path(__file__).resolve().parent
db=sqlite3.connect(root/'finance.sqlite'); db.row_factory=sqlite3.Row
manifest=json.loads((root/'manifest.json').read_text(encoding='utf-8'))
cases=json.loads((root/'golden_cases.json').read_text(encoding='utf-8'))
results={}
def check(name,value):
    results[name]=bool(value)
    if not value: raise AssertionError(name)
def query(sql): return [dict(r) for r in db.execute(sql)]
check('integrity',db.execute('PRAGMA integrity_check').fetchone()[0]=='ok')
check('foreign_keys',not query('PRAGMA foreign_key_check'))
for name,digest in manifest['source_hashes'].items(): check('source_hash_'+name,hashlib.sha256((root/'original'/name).read_bytes()).hexdigest()==digest)
for table,n in manifest['row_counts'].items(): check('row_count_'+table,db.execute('SELECT COUNT(*) FROM '+table).fetchone()[0]==n)
for c in cases:
    if c['reference_sql']: check(c['case_id']+'_result',query(c['reference_sql'])==c['expected_result'])
    if c['evidence_sql']: check(c['case_id']+'_evidence',query(c['evidence_sql'])==c['evidence'])
original=list(csv.DictReader((root/'original'/'transaction.csv').open(encoding='utf-8-sig')))
value=sum((-Decimal(r['transaction_amount']) for r in original if r['transaction_type']=='debit' and r['transaction_date'].startswith('2026-08')),Decimal(0))
check('original_august_total_independent',value==Decimal('75482133.83'))
check('original_august_database_total',db.execute("SELECT SUM(-amount_minor) FROM bank_transaction WHERE provenance='original_csv' AND transaction_type='debit' AND transaction_date>='2026-08-01' AND transaction_date<'2026-09-01'").fetchone()[0]==int(value*100))
check('cash_conservation',not query('SELECT t.transaction_id FROM bank_transaction t LEFT JOIN cash_allocation a USING(transaction_id) GROUP BY t.transaction_id HAVING COALESCE(SUM(a.amount_minor),0)<>t.amount_minor'))
check('confirmed_case_conservation',not query("SELECT case_id FROM v_reconciliation WHERE status='reconciled' AND (expected_minor<>observed_minor OR observed_minor<>matched_minor)"))
check('partial_invoice',db.execute("SELECT outstanding_minor FROM v_invoice_balance WHERE invoice_id='INV-PARTIAL'").fetchone()[0]==600000)
check('installment_invoice',db.execute("SELECT outstanding_minor FROM v_invoice_balance WHERE invoice_id='INV-INSTALLMENTS'").fetchone()[0]==0)
check('reversal_invoice',db.execute("SELECT outstanding_minor FROM v_invoice_balance WHERE invoice_id='INV-REVERSED'").fetchone()[0]==320000)
check('mismatch_delta',db.execute("SELECT expected_minor-observed_minor FROM v_reconciliation WHERE case_id='REC-MISMATCH'").fetchone()[0]==10000)
check('batch_bank_count',db.execute("SELECT COUNT(*) FROM case_observed WHERE case_id='REC-BATCH'").fetchone()[0]==1)
check('split_bank_count',db.execute("SELECT COUNT(*) FROM case_observed WHERE case_id='REC-SPLIT'").fetchone()[0]==2)
check('retry_two_attempts_one_payout',db.execute("SELECT COUNT(*) FROM payout_attempt WHERE payout_id='PAY-RETRY'").fetchone()[0]==2)
check('originals_not_automatically_unreconciled',db.execute("SELECT COUNT(*) FROM v_transaction_reconciliation WHERE reconciliation_status='not_assessed'").fetchone()[0]==19500)
check('all_generation_invariants_passed',all(manifest['checks'].values()))
report={'status':'passed','checks_passed':len(results),'golden_queries_replayed':sum(bool(c['reference_sql']) for c in cases),'behavioral_cases_for_assistant_testing':sum(not c['reference_sql'] for c in cases),'generation_invariants':len(manifest['checks']),'results':results,'note':'Data checks and SQL replay only; no assistant behavior or accuracy score has been measured.'}
(root/'validation_report.json').write_text(json.dumps(report,indent=2),encoding='utf-8')
print(json.dumps({k:v for k,v in report.items() if k!='results'},indent=2))
