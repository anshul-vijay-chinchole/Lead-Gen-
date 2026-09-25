"""Client lead delivery: the weekly "Hiring Signal Report".

One YAML per client in ``clients/`` (see ``client.py``). ``leadgen deliver
--client NAME`` runs the pipeline in delivery mode for that client, removes
anything already delivered to them (``ledger.py``), keeps only fresh job
signals, picks the best ``leads_per_week`` leads and writes a client-ready
CSV, an Excel file and a one-page HTML summary (``formats.py``), optionally
pushing to Google Sheets. A QA summary is printed after every run (``qa.py``).

Modules:
  rows.py     the client column set + honest email labels (the contract)
  client.py   load / validate a client file, turn it into a playbook
  ledger.py   per-client delivered-items history + per-client suppression list
  opening.py  optional "suggested opening line" (template, or AI with a cost cap)
  formats.py  CSV / XLSX / HTML / Google Sheets writers
  qa.py       the post-run QA summary
  run.py      deliver(): the orchestration
"""
