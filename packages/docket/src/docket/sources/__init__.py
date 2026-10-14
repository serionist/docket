"""Sources: the contract a source's mapper plugs into, running the mapper, and the ingest both routes share.

A source's skill and mapper live in the consuming project, under `.claude/skills/sources/<source>/` (DR-0108). Docket
keeps no source's code: it starts the mapper in its own process, checks every record it returns, computes every
version, and keeps held records out.
"""
