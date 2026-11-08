import asyncio
import json
import sys
from pathlib import Path
from unittest.mock import patch

ROOT_DIR = Path(__file__).resolve().parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from agents.tema_q.auditor import (
    detect_clinical_variances,
    filter_novel_secondary_sentences,
    merge_json_nodes,
)
from agents.tema_q.pipeline import call_llm_async, run_tema_q_synthesis
from agents.tema_q.subtopic_router import (
    route_and_prune_subtopics,
    sanitize_clinical_prose,
)
from agents.tema_q.topic_extractor import classify_medical_domain


# 3 Realistic Multi-Source Articles on the Same Topic with Different Headings & Noise
THREE_ARTICLES_PAYLOAD = [
    {
        "source_type": "pdf",
        "source_label": "Harrison Principles of Internal Medicine",
        "page": 154,
        "header": "Source (PDF - Harrison Principles of Internal Medicine - Page 154):\n",
        "raw_text": """# Falciparum Malaria
![Life cycle diagram](malaria_cycle.png)
> *Figure 1: Giemsa stain showing ring forms in erythrocytes.*

| Species | Fever Pattern | Recrudescence |
| --- | --- | --- |
| P. falciparum | Irregular / Continuous | Yes |

## Etiology and Vector Transmission
Falciparum malaria is a protozoan parasitic infection caused by Plasmodium falciparum and transmitted via the bite of an infected female Anopheles mosquito. Sporozoites injected during a blood meal travel rapidly to hepatic parenchymal cells where exoerythrocytic schizogony occurs. Ronald Ross enjoyed painting watercolor landscapes during his vacations in the 1890s.

## Clinical Manifestations
Patients present with high irregular fever, chills, diaphoresis, headache, and myalgias following an incubation period of 9 to 14 days. Severe falciparum malaria is defined by parasitemia > 5%, cerebral malaria with symmetric encephalopathy, hypoglycemia, lactic acidosis, and renal impairment (blackwater fever).

## Diagnostic Workup
Gold standard diagnosis is microscopic examination of thick and thin peripheral blood smears stained with Giemsa or Wright stain (see Figure 1). Thin smears confirm species via banana-shaped gametocytes, multiple ring forms per RBC (accolé/appliqué forms), and Maurer clefts. Rapid diagnostic tests detect HRP-2 antigen.

## Therapeutic Management
Uncomplicated falciparum malaria is treated with oral artemisinin-based combination therapy (ACT) such as artemether-lumefantrine. Severe malaria requires immediate parenteral antimalarial therapy and intensive care monitoring. Intravenous quinine loading dose is 20 mg/kg over 4 hours followed by 10 mg/kg every 8 hours.""",
    },
    {
        "source_type": "zim",
        "source_label": "ZIM: Plasmodium falciparum",
        "page": 1,
        "header": "Source (ZIM - Plasmodium falciparum):\n",
        "raw_text": """# Plasmodium falciparum
<table><tr><td>Taxon</td><td>Apicomplexa</td></tr></table>
<figure><img src="trophozoite.jpg"/><figcaption>Figure 2: Maurer clefts in infected RBC.</figcaption></figure>

## Parasite Lifecycle & Spread
Transmission occurs through the bite of the female Anopheles mosquito, which introduces motile sporozoites into the human bloodstream. PfEMP1 virulence proteins mediate cytoadherence of infected erythrocytes to capillary endothelium via ICAM-1 and CD36 receptors, causing microvascular sequestration. The hospital cafeteria on the third floor closes at 6 PM on weekends.

## Signs, Symptoms & Complications
Clinical presentation includes paroxysmal fever, splenomegaly, hepatomegaly, and hemolytic anemia. Major life-threatening complications include cerebral malaria (coma, seizures), non-cardiogenic pulmonary edema (ARDS), acute tubular necrosis, and algid malaria (shock).

## Treatment & Chemoprophylaxis
The drug of choice for severe falciparum malaria is intravenous artesunate 2.4 mg/kg IV administered at 0, 12, and 24 hours, then once daily. In pregnancy (first trimester), IV artesunate remains superior to quinine for severe disease. Primary chemoprophylaxis for travelers to endemic regions uses atovaquone-proguanil, doxycycline 100 mg daily, or mefloquine.""",
    },
    {
        "source_type": "obsidian",
        "source_label": "Infectious_Diseases_High_Yield_Notes",
        "page": 1,
        "header": "Source (Obsidian - Infectious_Diseases_High_Yield_Notes):\n",
        "raw_text": """# Malaria Clinical Pearl Notes
![[banana_gametocyte.webp]]
Table 3: Antimalarial resistance patterns in Southeast Asia.

## Microscopy & Lab Identification
Peripheral smear microscopy: Thick smear has highest sensitivity for parasite detection; thin smear is used for species identification. P. falciparum shows delicate ring trophozoites, double chromatin dots (headphone appearance), Maurer clefts, and crescent/banana-shaped gametocytes. HRP-2 rapid antigen test remains positive for weeks after cure.

## Management Protocol & Dosing Cutoffs
Severe malaria criteria in endemic adults require parasitemia > 10% or severe organ dysfunction. For intravenous quinine therapy in patients who recently received mefloquine, administer a reduced maintenance dose of 10 mg/kg every 12 hours without a loading dose to prevent fatal cardiotoxicity and QT prolongation. Monitor blood glucose closely due to quinine-induced hyperinsulinemic hypoglycemia.""",
    },
]

for doc in THREE_ARTICLES_PAYLOAD:
    doc["header"] = doc["header"] + doc["raw_text"]


async def _check_lm_studio_reachable() -> bool:
    try:
        resp = await call_llm_async(
            "Reply with the single word OK.", "Ping", max_tokens=10
        )
        return bool(resp and len(resp.strip()) > 0)
    except Exception:
        return False


async def main() -> None:
    report_lines = ["# End-to-End 3-Article Subtopic Synthesis Verification Report\n"]

    # 1. Domain Classification Verification
    domain_Checks = {
        "Falciparum Malaria": classify_medical_domain("Falciparum Malaria"),
        "Clostridium tetani": classify_medical_domain("Clostridium tetani"),
        "Amyloidosis": classify_medical_domain("Amyloidosis"),
        "Amoxicillin": classify_medical_domain("Amoxicillin"),
        "Brachial plexus": classify_medical_domain("Brachial plexus"),
        "Rigor mortis": classify_medical_domain("Rigor mortis", "Forensic autopsy"),
        "Primary prevention": classify_medical_domain("Primary prevention", "Immunization"),
    }
    report_lines.append("## 1. Domain Classification Audit")
    for k, v in domain_Checks.items():
        report_lines.append(f"- **{k}** -> `{v}`")
    report_lines.append("")

    # 2. Pure-Prose Sanitization Verification
    report_lines.append("## 2. Pure-Prose Sanitization Audit (Images, Tables, Captions Stripped)")
    for doc in THREE_ARTICLES_PAYLOAD:
        cleaned = sanitize_clinical_prose(doc["raw_text"])
        assert "malaria_cycle.png" not in cleaned, "Markdown image leaked!"
        assert "Figure 1:" not in cleaned, "Figure 1 caption leaked!"
        assert "Figure 2:" not in cleaned, "HTML figcaption leaked!"
        assert "| P. falciparum |" not in cleaned, "Markdown table leaked!"
        assert "<table>" not in cleaned, "HTML table leaked!"
        assert "banana_gametocyte.webp" not in cleaned, "Obsidian image embed leaked!"
        assert "Table 3:" not in cleaned, "Table 3 caption leaked!"
        report_lines.append(
            f"- **{doc['source_label']}**: Raw `{len(doc['raw_text'].split())}` words -> Sanitized `{len(cleaned.split())}` words (100% tables/images/captions removed)"
        )
    report_lines.append("")

    # 3. Subtopic Routing & Solar Sentence Pruning across 3 Differently-Headed Articles
    buckets = route_and_prune_subtopics("Falciparum Malaria", THREE_ARTICLES_PAYLOAD)
    report_lines.append(f"## 3. Subtopic Routing Across 3 Articles ({len(buckets)} Subtopic Buckets Created)")
    assert len(buckets) >= 3, f"Expected >= 3 routed subtopic buckets, got {len(buckets)}"

    for b in buckets:
        p_words = len(b.primary_text.split())
        sec_sources = [s["source_label"] for s in b.secondary_entries]
        report_lines.append(f"### Subtopic: `{b.display_name}` (`{b.subtopic_id}`)")
        report_lines.append(f"- **Canonical Question Anchor**: *{b.question_anchor}*")
        report_lines.append(f"- **Primary Source ({b.primary_source_label}, p.{b.primary_page})**: `{p_words}` words")
        report_lines.append(f"  > {b.primary_text}")
        for idx_s, sec in enumerate(b.secondary_entries, 1):
            s_words = len(sec["text"].split())
            report_lines.append(f"- **Matched Secondary #{idx_s} ({sec['source_label']}, p.{sec['page']})**: `{s_words}` words")
            report_lines.append(f"  > {sec['text']}")
        report_lines.append("")

    # 4. Run Full Synthesis Pipeline (Live LM Studio if running; plus deterministic mock verification if offline)
    lm_live = await _check_lm_studio_reachable()
    report_lines.append(f"## 4. Full 3-Article Synthesis Execution (`LM Studio Live = {lm_live}`)")

    with patch("agents.tema_q.pipeline.resolve_documents", return_value=THREE_ARTICLES_PAYLOAD):
        result = await run_tema_q_synthesis(
            topic="Falciparum Malaria",
            search_results={"pdf": [{}], "zim": [{}], "obsidian": [{}]},
            mcq_context={
                "question": "A traveler returning from sub-Saharan Africa presents with coma, parasitemia of 8%, and banana-shaped gametocytes on Giemsa smear. What is the intravenous drug of choice?",
                "options": {
                    "A": "Oral Chloroquine",
                    "B": "Intravenous Artesunate",
                    "C": "Primaquine",
                    "D": "Oral Doxycycline monotherapy",
                },
                "correct_answer": "B",
            },
            skip_lm=False,
        )

    unified_raw = result.get("unified_article") or "{}"
    unified_obj = json.loads(unified_raw)
    enhanced_md = result.get("enhanced_explanation") or ""
    variance_audit = result.get("variance_audit") or ""

    # Verify word counts per subtopic or overall points
    subtopics_list = unified_obj.get("subtopics") or [unified_obj]
    report_lines.append("### Subtopic Word Count & Budget Verification")
    for st_node in subtopics_list:
        st_name = st_node.get("subtopic") or st_node.get("section") or "Subtopic"
        pts = st_node.get("clinical_points") or []
        st_words = sum(len(str(p.get("point", "")).split()) for p in pts if isinstance(p, dict))
        assert st_words <= 1000, f"Subtopic '{st_name}' exceeded 1000-word limit ({st_words} words)!"
        report_lines.append(f"- **{st_name}**: `{len(pts)}` clinical points, `{st_words}` total words (Limit: `<= 1000` words) - **PASS**")

    report_lines.append("\n### Variance / Discrepancy Audit Output")
    report_lines.append("```markdown\n" + (variance_audit or "No variance detected") + "\n```")

    report_lines.append("\n### Final Rendered Clinical Markdown (`enhanced_explanation`)")
    report_lines.append("```markdown\n" + enhanced_md + "\n```")

    report_path = ROOT_DIR / "verification_report.md"
    report_path.write_text("\n".join(report_lines), encoding="utf-8")
    print(f"Verification complete! Report written to {report_path}")


if __name__ == "__main__":
    asyncio.run(main())
