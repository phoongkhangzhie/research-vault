# Corpus (sanitized real-shape slice)

A sanitized slice of a real `review-curate` output, kept as a regression
pin against a fifth divergent corpus-row parser. 18 of the 20 rows below
are taken verbatim (citekey + title, abstract truncated) from a real
curated corpus of publicly available papers — a mix of arXiv-id and
DOI-form citekeys, exactly the shape that a `/`-less citekey charset
silently drops. The last two rows (a compound `[LEG-1][NEW]` annotation
and an `[IN-CORPUS:*]` row) are appended by hand — the real slice this
was sourced from happened not to contain either shape, but both are a
real `review-curate`/`review-snowball` output shape and must round-trip
correctly too.

| Annotation | Citekey | Title | Abstract/TL;DR |
|---|---|---|---|
| [NEW] | 10.1016/j.knosys.2026.116598 | PathSymphony: Harmonizing symbolic planning and Large Language Models for curriculum-guided mathematical reasoning (arith) |  |
| [NEW] | 2203.11171 | Self-Consistency Improves Chain of Thought Reasoning in Language Models (arith/cot-method) | Chain-of-thought prompting combined with pre-trained large language models has achieved encouraging results. |
| [NEW] | 2112.11446 | Scaling Language Models: Methods, Analysis & Insights from Training Gopher (arith) | Language modelling provides a step towards intelligent communication systems. |
| [NEW] | 2110.14168 | Training Verifiers to Solve Math Word Problems (arith) | State-of-the-art language models can match human performance on many tasks. |
| [NEW] | 10.18653/v1/2021.findings-emnlp.195 | Generate & Rank: A Multi-task Framework for Math Word Problems (arith) | Math word problem (MWP) is a challenging and critical task in NLP. |
| [NEW] | 10.1609/aaai.v36i11.21723 | MWPToolkit: An Open-Source Framework for Deep Learning-Based Math Word Problem Solvers (arith) | While MWP solving has emerged as a popular field of study. |
| [NEW] | 2108.07732 | Program Synthesis with Large Language Models (arith) | This paper explores the limits of the current generation of large language models. |
| [NEW] | 10.18653/v1/2021.acl-short.49 | Measuring and Improving BERT's Mathematical Abilities by Predicting the Order of Reasoning (arith) | Imagine you are in a supermarket with two bananas and want four apples. |
| [NEW] | 10.18653/v1/2020.acl-main.89 | Injecting Numerical Reasoning Skills into Language Models (arith) | Large pre-trained language models encode substantial linguistic information. |
| [NEW] | 10.18653/v1/P17-1015 | Program Induction by Rationale Generation (arith) | Solving algebraic word problems requires executing a series of arithmetic operations. |
| [NEW] | 2607.07779 | From Solvers to Research: LLM-Driven Formal Mathematics at the Research Frontier (arith) | Recent developments in AI for Mathematics have achieved remarkable success. |
| [NEW] | 2607.04572 | Detecting Answer-Driven Reasoning in LLM-Based Educational Tutors (arith) | LLM tutors often produce fluent step-by-step explanations. |
| [NEW] | 10.52202/068431-1800 | Chain of Thought Prompting Elicits Reasoning in Large Language Models (arith/cot-method) | We explore how generating a chain of thought significantly improves reasoning. |
| [NEW] | 10.48550/arXiv.2212.08061 | On Second Thought, Let's Not Think Step by Step! (arith/counter) | Generating a Chain of Thought has been shown to consistently improve LLM performance. |
| [NEW] | 2211.12588 | Program of Thoughts Prompting (arith/cot-method) | Recently there has been significant progress in teaching language models step-by-step reasoning. |
| [NEW] | 10.48550/arXiv.2210.01240 | Language Models Are Greedy Reasoners (arith/counter) | Large language models have shown remarkable reasoning capabilities given chain-of-thought prompts. |
| [NEW] | 10.48550/arXiv.2210.00720 | Complexity-Based Prompting for Multi-Step Reasoning (cot-method) | We study the task of prompting large-scale language models to perform multi-step reasoning. |
| [NEW] | 10.48550/arXiv.2209.14610 | Dynamic Prompt Learning via Policy Gradient for Semi-structured Mathematical Reasoning (arith) | Mathematical reasoning presents unique challenges for machines in abstract thinking. |
| [LEG-1][NEW] {arith,cot-method} | 10.1145/3593013.3594067 | On the Advance of Making Language Models Better Reasoners (arith/cot-method) | Illustrative compound-annotation row (leg tag + NEW status + trailing concept tags). |
| [IN-CORPUS:brown2021] | brown2021 | Language Models are Few-Shot Learners | Illustrative already-vetted row — must be excluded from the Phase-2 fan-out. |
