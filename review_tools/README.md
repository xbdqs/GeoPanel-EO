# Reviewer-oriented tests and scientific-validation scripts

The `tests_review/` suite includes deterministic synthetic tests that do not require raw Chicago Daymet/NLCD files. These tests exercise explicit temporal semantics, support overlap, preprocessing hash guards and altered/missing inputs.

Some `review_tools/chicago_*.py` scripts are real-data validation harnesses. They require separately obtained original Chicago data in a local `review_data/chicago` workspace and may have Windows path defaults. Their archived inputs must not be uploaded to the public GitHub repository. `chicago_nlcd_provenance_audit_corrected.py` supersedes the stricter initial historical audit script; the initial code remains for transparent reproducibility of the diagnostic progression.

A synthetic test suite is not evidence of a successful independent reinstall or a resampling method's scientific superiority. Consult documented real-data audit files and their stated validation boundaries.
