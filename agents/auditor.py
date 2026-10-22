"""
auditor.py - Compatibility re-exporter forwarding detect_clinical_variances
and merge_json_nodes from agents.tema_q.auditor (Section 2 & 3).
"""

import os

# Enforce Invariant 1: Zero VRAM for backend processes
os.environ["CUDA_VISIBLE_DEVICES"] = ""

from agents.tema_q.auditor import detect_clinical_variances, merge_json_nodes

__all__ = ["detect_clinical_variances", "merge_json_nodes"]
