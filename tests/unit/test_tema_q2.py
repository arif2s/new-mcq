from agents.tema_q.prompts import PASS1_CLINICAL_SYSTEM_PROMPT, PASS2_AUDIT_SYSTEM_PROMPT

print("Pass 1 Prompt Exists:", bool(PASS1_CLINICAL_SYSTEM_PROMPT))
print("Pass 2 Prompt Exists:", bool(PASS2_AUDIT_SYSTEM_PROMPT))
print("Discrepancies string in Pass 2:", "### Discrepancies & Variances" in PASS2_AUDIT_SYSTEM_PROMPT)
