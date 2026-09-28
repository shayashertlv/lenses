"""Modeler: an AI runtime author writes and revises Blender construction programs for a pair of glasses from a
few catalog photographs; the host executes them headlessly, observes renders and measurements, exports the AR GLB
through the existing contract and keeps the strongest candidate.

Isolated development path (2026-09-26). It imports from ``bsa/`` and ``reconstruction/``; changes to them (the
translucent export and contract, the AR runtime's translucent twin in ``ar/src``) are made there, with their own tests.
Nothing here changes the live application. See DESIGN.md in this folder.
"""
