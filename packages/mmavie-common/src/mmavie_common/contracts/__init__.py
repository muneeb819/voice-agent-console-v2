"""Versioned data contracts shared between MMA-VIE services and Console V2.

Contract version is bumped on any breaking change; services reject payloads whose
`contract_version` major differs from theirs.
"""

CONTRACT_VERSION = "1.0"
