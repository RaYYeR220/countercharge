"""Offline builder that turns CMS public downloads into ``refdata.sqlite``.

Nothing under this package is imported by the audit engine itself -- it is
a standalone tool, run occasionally (`python -m countercharge_engine.refdata.build`)
to produce the sqlite file the engine reads via
:class:`countercharge_engine.refdata.sqlite.SqliteRefData`.
"""
