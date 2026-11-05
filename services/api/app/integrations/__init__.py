"""Connections to services the student already keeps material in.

Each integration reads from the outside service on demand and keeps only the
search index here — never a copy of the file — so linking a folder costs a
few hundred kilobytes of text per deck rather than the deck itself.
"""
