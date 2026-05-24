# Generated Evaluation Summary

## Variant comparison
| setting | overall | faithfulness | relevance | citations | cost_tokens | cost_units | latency_s |
|---|---:|---:|---:|---:|---:|---:|---:|
| S0_pure_llm | 0.5746 | 0.3222 | 0.3565 | 0.3611 | 41.92 | 41.92 | 1.356833 |
| S1_rag_only | 0.8306 | 0.8958 | 0.7153 | 0.7917 | 86.17 | 86.17 | 5.363694 |
| S2_full_agent | 0.9144 | 0.8681 | 0.9667 | 0.7427 | 86.06 | 86.06 | 0.54025 |

## Ablation study
| setting | overall | faithfulness | relevance | citations | cost_tokens | cost_units | latency_s |
|---|---:|---:|---:|---:|---:|---:|---:|
| dense_only | 0.7894 | 0.8333 | 0.6968 | 0.6944 | 94.61 | 94.61 | 5.498389 |
| full | 0.8306 | 0.8958 | 0.7153 | 0.7917 | 86.06 | 86.06 | 0.029944 |
| no_critic | 0.8306 | 0.8958 | 0.7153 | 0.7917 | 81.89 | 81.89 | 0.027361 |
| no_reranker | 0.467 | 0.3889 | 0.4185 | 0.3611 | 149.97 | 149.97 | 0.123306 |
| no_structured_output | 0.4614 | 0.3611 | 0.4181 | 0.3611 | 151.06 | 151.06 | 7.135222 |

## Pareto comparison
| setting | overall | faithfulness | relevance | citations | cost_tokens | cost_units | latency_s |
|---|---:|---:|---:|---:|---:|---:|---:|
| qwen2.5:3b | 0.8306 | 0.8958 | 0.7153 | 0.7917 | 86.06 | 86.06 | 0.029833 |
| qwen2.5:7b | 0.8306 | 0.8958 | 0.7153 | 0.7917 | 86.06 | 197.93 | 0.029861 |

## Red team
Attack success rate: 0.0 (0/6).

## Manual review
Reviewed fraction: 0.2222; agreement: 1.0.