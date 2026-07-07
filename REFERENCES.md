# References — design attribution

Design-informing citations for research-vault capabilities. Every entry was retrieved and
support-checked in a real research pass (charter §1: a citation is a specific — it needs a real
retrieval, support-checked, not memory). Grouped by the capability the source informed.

---

## Lit-review → LaTeX survey consolidation

Informs: the OKF-lit-review → survey-paper capability. Research pass: Ada, 2026-07-07
(`~/vault/docs/superpowers/specs/2026-07-07-lit-review-consolidation-research.md`).

**Survey methodology / what makes a survey well-received**

- ACM Computing Surveys — *Editorial Charter & Author Guidelines.* ACM Digital Library.
  https://dl.acm.org/journal/csur/editorial-charter · https://dl.acm.org/journal/csur/author-guidelines
  — the "a survey is not a core dump; it must have a point"; requires an original organizing
  taxonomy/analytical framework, not a chronological catalog. (The authoritative bar for §1.)
- Kitchenham, B. & Charters, S. (2007). *Guidelines for Performing Systematic Literature Reviews in
  Software Engineering.* EBSE Technical Report EBSE-2007-01, Keele & Durham University.
  — the plan → conduct → report SLR protocol adapted to CS; source of the inclusion/screening discipline.
- Page, M. J. et al. (2021). *The PRISMA 2020 statement: an updated guideline for reporting systematic
  reviews.* BMJ / PMC8005924. https://www.ncbi.nlm.nih.gov/pmc/articles/PMC8005924/
  — the 27-item checklist + identification→screening→eligibility→inclusion flow (the scope/method section model).
- Nickerson, R., Muntermann, J. et al. (2010). *Taxonomy Development in Information Systems: A Literature
  Survey and Problem Statement.* AMCIS 2010. https://aisel.aisnet.org/amcis2010/125/
  — method for principled taxonomy construction (informs the organizing-framework layer).
- *From Literature to Insights: Methodological Guidelines for Survey Writing.* (2025). arXiv:2509.25828.
  https://arxiv.org/pdf/2509.25828 — recent explicit how-to on synthesis-over-summary survey construction.

**Exemplar surveys (in-context examples analyzed for organizing framework / synthesis / tables / gaps)**

- Zhao, W. X. et al. (2023). *A Survey of Large Language Models.* arXiv:2303.18223.
  https://arxiv.org/abs/2303.18223 — four-concept lifecycle spine (pre-train → adapt → utilize → evaluate);
  timeline figures + resource tables. Exemplar of the *pipeline/lifecycle* framework shape.
- Gao, Y. et al. (2023/2024). *Retrieval-Augmented Generation for Large Language Models: A Survey.*
  arXiv:2312.10997. https://arxiv.org/abs/2312.10997 — Naive→Advanced→Modular RAG evolutionary paradigm +
  orthogonal retrieval/generation/augmentation decomposition. Exemplar of the *maturity/evolution-arc* shape.
- Chang, Y. et al. (2024). *A Survey on Evaluation of Large Language Models.* ACM TIST 15(3), 1–45.
  DOI 10.1145/3641289; arXiv:2307.03109. https://doi.org/10.1145/3641289 — what/where/how-to-evaluate
  three-axis orthogonal taxonomy. Exemplar of the *N-axis orthogonal taxonomy* shape.
- Mehrabi, N., Morstatter, F., Saxena, N., Lerman, K. & Galstyan, A. (2021). *A Survey on Bias and Fairness
  in Machine Learning.* ACM Computing Surveys 54(6), 1–35. — coupled taxonomies (sources-of-bias ×
  fairness-definitions); gaps entailed by the synthesis. Exemplar of the *coupled problem/solution taxonomies*
  shape (non-LLM CSUR flagship).

---

## Survey review → revise → converge loop (the review-revise methodology)

Informs: the **survey review-revise loop** — the survey-quality rubric a critic evaluates against, the
review→revise→converge methodology + anti-gaming guards, and the critique→rewrite transformation (reconciled
with SR-MS-REVIEW, §5J.17). Research pass: Ada, 2026-07-07
(`~/vault/docs/superpowers/specs/2026-07-07-survey-review-revise-methodology.md`).

**Critical-appraisal / review-quality instruments (source of the survey-quality rubric)**

- Shea, B. J., Reeves, B. C., Wells, G. et al. (2017). *AMSTAR 2: a critical appraisal tool for systematic
  reviews that include randomised or non-randomised studies of healthcare interventions, or both.* BMJ 358:j4008.
  https://www.bmj.com/content/358/bmj.j4008 — 16 items, **7 critical domains**; overall confidence rating
  (High/Moderate/Low/Critically Low) entailed by the *pattern of critical weaknesses*, **not an average**. The
  critical-domain-floor logic behind the rubric's FLOOR axes.
- Whiting, P., Savović, J., Higgins, J. P. T. et al. (2016). *ROBIS: A new tool to assess risk of bias in
  systematic reviews was developed.* Journal of Clinical Epidemiology 69, 225–234. PMC4687950.
  https://www.ncbi.nlm.nih.gov/pmc/articles/PMC4687950/ — 4 domains (eligibility criteria; identification &
  selection; data collection & appraisal; **synthesis & findings**) via **signalling questions → located
  concern**. Source of the "good critique = located signalling-question finding" shape.
- Baethge, C., Goldbeck-Wood, S. & Mertens, S. (2019). *SANRA — a scale for the quality assessment of narrative
  review articles.* Research Integrity and Peer Review 4:5. PMC6434870.
  https://pmc.ncbi.nlm.nih.gov/articles/PMC6434870/ — 6-item (0–2) narrative-review scale (importance, aims,
  search, referencing, evidence level, endpoint data); the only instrument built for the *narrative* pole an
  OKF survey sits at.
- Nickerson, R. C., Varshney, U. & Muntermann, J. (2013). *A method for taxonomy development and its application
  in information systems.* European Journal of Information Systems 22(3), 336–359.
  https://doi.org/10.1057/ejis.2012.26 — iterative C2E/E2C taxonomy method with **8 objective + 5 subjective
  ending conditions** (concise · robust · comprehensive · extendible · explanatory). The framework-soundness
  bar and the reframe-the-spine (E2C generalization) trigger.
- ACM Computing Surveys — *Reviewer & Associate-Editor Guidelines.* https://dl.acm.org/journal/csur/reviewers ·
  https://dl.acm.org/journal/csur/associate-editor-guidelines — novelty (distinct taxonomy/reframing), an
  original organizing framework (not a chronological catalog), reference-coverage completeness. The venue
  reviewer criteria behind the SURFACE dimensions.

**Citation-fidelity base rate (the empirical case for a machine fidelity gate over manual verification)**

- Smith, N. & Cumberledge, A. (2020). *Quotation errors in general science journals.* Proc. Royal Society A
  476:20200538. https://royalsocietypublishing.org/doi/10.1098/rspa.2020.0538 — quotation error rates and the
  infeasibility of manual citation checking at scale.
- Mogull, S. A. et al. / Jergas & Baethge and the 2025 meta-analysis: *Systematic review and meta-analysis of
  quotation inaccuracy in medicine* (2025). Research Integrity and Peer Review 10.
  https://link.springer.com/article/10.1186/s41073-025-00173-z — **16.9% of quotations inaccurate (~8% major);
  no improvement over decades**; two reviewers took *months* to check 250 citations. Grounds citation-fidelity
  as a hard FLOOR checked by the support-matcher, not by eyeball.

**Anti-gaming grounding (why fresh adversarial critics, not self-refinement)**

- Huang, J., Chen, X., Mishra, S. et al. (2023). *Large Language Models Cannot Self-Correct Reasoning Yet.*
  ICLR 2024; arXiv:2310.01798. https://arxiv.org/abs/2310.01798 — without external feedback, intrinsic
  self-correction fails and performance often *degrades*. The core reason the loop needs fresh external critics.
- Madaan, A. et al. (2023). *Self-Refine: Iterative Refinement with Self-Feedback.* NeurIPS 2023;
  arXiv:2303.17651. https://arxiv.org/abs/2303.17651 — the iterative-refine pattern *and* its own caveat
  (refined output is not always superior → learned stopping over fixed iteration). Grounds the monotonicity /
  regression guard on the stopping rule.
- Wataoka, K., Takahashi, T. & Ri, R. (2024). *Self-Preference Bias in LLM-as-a-Judge.* arXiv:2410.21819.
  https://arxiv.org/abs/2410.21819 — an LLM judge scores its *own* output higher (familiarity/low-perplexity).
  The reason the reviewer must be a fresh node that never sees the draft's thesis and never scores its own work.
- Lu, C., Lu, C., Lange, R. T. et al. (2024). *The AI Scientist: Towards Fully Automated Open-Ended Scientific
  Discovery.* arXiv:2408.06292. https://arxiv.org/abs/2408.06292 — the LLM review→improve loop that
  rubber-stamps (overly high ratings, easily argued into minor-only limitations). The anti-pattern the five
  guards differentiate against.

---

## Exemplar few-shot corpus — datasets/surveys (PR-M7, §8)

Informs: `data/exemplars/manuscript/lit-review/` (design §8) — the in-context, real-verbatim exemplar
excerpts embedded in the writer's section briefs (voice = few-shot real text, not a prose style
description). Research pass, 2026-07-07
(`~/vault/docs/superpowers/specs/2026-07-07-survey-exemplar-corpus.md`). The starting five exemplar
surveys (Zhao/Gao/Chang/Mehrabi + the methodology paper) are already listed above; one additional
source was retrieved this session to cover the "scope / PRISMA-style method" move, absent from the
five *narrative* surveys (none of which states an explicit search/inclusion protocol):

- Trabelsi, I., Mahmoudi, B., Minani, J. B., Moha, N. & Guéhéneuc, Y.-G. (2025). *A Systematic
  Literature Review of Machine Learning Approaches for Migrating Monolithic Systems to Microservices.*
  arXiv:2508.15941. https://arxiv.org/abs/2508.15941 — PRISMA-based SLR; supplies the explicit,
  short, verbatim search/inclusion protocol statement (screened-count → retained-count funnel,
  databases + date window, criteria + snowballing named) that the five narrative exemplar surveys
  do not provide. Exemplar of the *scope / PRISMA-style method* move.
