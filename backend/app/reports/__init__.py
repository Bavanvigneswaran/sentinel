"""PDF/CSV rendering and email delivery for scheduled and on-demand reports.

Every renderer here takes a services/report_service.ReportBundle — the one
computation both the API's download endpoints and app/workers/report_worker.py's
scheduled emails render from, so a PDF a user downloads and one emailed to
them the same morning can never show different numbers.
"""
