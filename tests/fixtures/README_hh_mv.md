# HH/MV official-notice regression fixtures

Retrieved 2026-10-16. These are uncorrected text excerpts extracted with PyMuPDF
from the linked official publications. Soft hyphens and original line breaks are
intentional. Tests use an explicit reference date so they do not expire.

| Fixture | Original PDF | Regression |
| --- | --- | --- |
| hh-00-notices.txt | https://www.luewu.de/wp-content/uploads/2026/10/AANZ_HH_2026-82.pdf (pages 31–32) | Split month/court names, three proceedings, property versus court postcode |
| hh-08-notices.txt | https://www.luewu.de/wp-content/uploads/2026/09/AANZ_HH_2026-74.pdf (pages 14–15) | Neighboring court and notice must not leak |
| mv-00-notices.txt | https://www.mecklenburg-vorpommern.de/sites/default/files/2026-10/Amtlicher_Anzeiger_M-V_42_2026.pdf | Greifswald proceedings, publication date versus auction date |
| mv-08-notices.txt | https://www.mecklenburg-vorpommern.de/sites/default/files/2026-08/Amtlicher%20Anzeiger%20M-V_34_2026.pdf | Unrelated liquidation address after the last auction |
| mv-30-notices.txt | https://www.mecklenburg-vorpommern.de/sites/default/files/2026-03/Amtlicher%20Anzeiger%20M-V_11_2026.pdf | Cross-references between two proceedings |
| mv-cancellation-notices.txt | https://www.mecklenburg-vorpommern.de/sites/default/files/2026-08/Amtlicher%20Anzeiger%20M-V_33_2026.pdf | Cancellation of 41 K 24/25 must not cancel 15 K 35/25 |

Run with `python tests/test_official_notice_parsers.py` (the existing workflow
entry point), or `python tests/test_hh_mv_notices.py`.

The new parser is limited to HH and MV. The shared BW/SH parser is unchanged.
The merge only replaces HH/MV supplemental records from successfully parsed
issues; source/parser failures retain the affected issue's previous records.
