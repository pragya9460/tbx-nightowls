# Banking Glossary (demo knowledge base)

## UTR number
A UTR (Unique Transaction Reference) number is a unique code generated for
every bank transaction in India. It appears on NEFT, RTGS, IMPS and UTI
transfers and lets banks trace a specific payment end-to-end.

## Transaction reference ID
The transaction_reference_id is the sending system's own identifier for a
payment. Unlike the UTR (issued by the banking rails), the reference id is
assigned by the originating application.

## Available balance
Available balance is the money you can spend right now: it excludes holds,
pending settlements and earmarked amounts. In this dataset the
account.available_balance column is authoritative — balances are read
directly, never reconstructed from transaction sums.

## IFSC bank codes
Indian Financial System Code (IFSC) prefixes identify banks: HDFC (HDFC
Bank), ICIC (ICICI Bank), SBIN (State Bank of India), UTIB (Axis Bank),
KKBK (Kotak Mahindra Bank), CNRB (Canara Bank), UBIN (Union Bank), AUBL (AU
Small Finance Bank), TMBL (Tamilnad Mercantile Bank), RATN (RBL Bank).
