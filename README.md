# EU AI Act / GDPR Multi-Agent Compliance Assistant

Toto je riešenie zadania `aktivitka c.3`, úroveň 2. Projekt implementuje lokálny agentický RAG systém pre CTO startupu, ktorý odpovedá po slovensky na otázky o EU AI Act a GDPR s validným JSON výstupom, citáciami, guardrails, benchmarkom, red-team testami, abláciami a trace logmi.

Tento README používa iba Docker príkazy: od buildu, cez Ollamu, testy, evaluáciu, živé demo, LaTeX PDF až po vypnutie a vymazanie kontajnerov/artefaktov.

## 1. Build image

Spusti v koreňovom priečinku projektu:

```powershell
docker compose build
```

## 2. Spustenie Ollamy v Dockeri

Ollama služba je definovaná v `docker-compose.yml` a používa `gpus: all`, takže na stroji s NVIDIA kartou vie využiť GPU.

```powershell
docker compose --profile ollama up -d ollama
```

Stiahni potrebné modely do Ollama kontajnera:

```powershell
docker compose exec ollama ollama pull qwen2.5:3b
docker compose exec ollama ollama pull qwen2.5:7b
docker compose exec ollama ollama pull qwen2.5:14b
docker compose exec ollama ollama pull nomic-embed-text
```

Rýchla kontrola modelu:

```powershell
docker compose exec ollama ollama run qwen2.5:3b "Odpovedz iba OK"
docker compose exec ollama ollama ps
```

Pri GPU behu by `ollama ps` mal ukázať, že model beží na GPU. Ak nie, obnov kontajner:

```powershell
docker compose --profile ollama up -d --force-recreate ollama
```

## 3. Testy

Unit testy:

```powershell
docker compose --profile tools run --rm test
```

Rýchly smoke eval:

```powershell
docker compose --profile tools --profile ollama run --rm -e OLLAMA_BASE_URL=http://ollama:11434 quick
```

## 4. Plná evaluácia zadania

Plný beh spustí S0/S1/S2, ablations, red-team, Pareto porovnanie, trace logy a LLM judge s `qwen2.5:14b`.

```powershell
docker compose --profile ollama run --rm -e OLLAMA_BASE_URL=http://ollama:11434 app
```

Výsledky vzniknú v lokálnom priečinku `outputs/`, pretože je namapovaný do kontajnera:

- `outputs/metrics.csv`
- `outputs/metrics.json`
- `outputs/summary.json`
- `outputs/report_summary.md`
- `outputs/trace_samples.json`
- `outputs/traces/*.json`
- `outputs/ablation_quality.svg`
- `outputs/pareto_quality_cost.svg`

## 5. Jedna vlastná otázka cez CLI demo

```powershell
docker compose --profile tools --profile ollama run --rm -e OLLAMA_BASE_URL=http://ollama:11434 -e QUESTION="Musí chatbot oznámiť používateľovi, že komunikuje s AI?" demo
```

Ďalší príklad otázky:

```powershell
docker compose --profile tools --profile ollama run --rm -e OLLAMA_BASE_URL=http://ollama:11434 -e QUESTION="Aká je presná suma pokuty pre našu firmu za incident z minulého týždňa?" demo
```

## 6. Živé web demo

Web demo obsahuje tri scenáre: úspešný prípad, zlyhanie/limitáciu a zdržanie sa odpovede. Dá sa vložiť aj vlastná otázka a vlastná poznámka.

Spustenie:

```powershell
docker compose --profile tools --profile ollama run --rm --service-ports -e OLLAMA_BASE_URL=http://ollama:11434 web-demo
```

Potom otvor v prehliadači `http://127.0.0.1:8080`.

Ak máš Ollamu spustenú mimo compose, napríklad priamo vo Windows, použi:

```powershell
docker compose --profile tools run --rm --service-ports -e OLLAMA_BASE_URL=http://host.docker.internal:11434 web-demo
```

## 7. Oficiálna báza znalostí

Projekt obsahuje plné lokálne snapshoty oficiálnych zdrojov v `data/official/raw/`. Aktuálne overený corpus má:

- 42 dokumentov spolu,
- 7 oficiálnych snapshotov,
- približne 151 000 slov oficiálneho textu,
- 768 chunkov, z toho 733 z oficiálnych snapshotov.

Ak chceš v Dockeri nanovo pregenerovať základný corpus z repozitára:

```powershell
docker compose --profile tools run --rm quick
```

Tento príkaz spustí rýchly eval a zároveň zabezpečí materializáciu potrebných dát v namapovanom priečinku `data/`.

## 8. LaTeX report cez Docker

Odovzdávací Dockerfile neobsahuje LaTeX. PDF je už v repozitári ako `report/report_level2.pdf`, takže pri bežnom overení zadania netreba LaTeX kompilovať.

Ak chceš PDF z `.tex` vygenerovať znova, použi externý jednorazový TeX Live image. Tento image sa nevytvára spolu s projektom; Docker ho pri prvom použití stiahne z Docker Hubu. Ak nemáš internet alebo image nie je lokálne stiahnutý, tento krok nebude fungovať.

Voliteľné predtiahnutie image:

```powershell
docker pull texlive/texlive:latest
```

Kompilácia reportu:

```powershell
docker run --rm -v "${PWD}:/work" -w /work/report texlive/texlive:latest latexmk -pdf -interaction=nonstopmode -halt-on-error report_level2.tex
```

Zdroj reportu je `report/report_level2.tex`, výsledok je `report/report_level2.pdf`.

### LaTeX Workshop vo VS Code/Cursor

V repozitári je pripravené nastavenie `.vscode/settings.json`. LaTeX Workshop vďaka nemu kompiluje otvorený `.tex` súbor cez externý Docker image `texlive/texlive:latest`, nie cez projektový `Dockerfile`.

Postup v IDE:

1. Otvor celý priečinok projektu vo VS Code alebo Cursor.
2. Nainštaluj rozšírenie `LaTeX Workshop`.
3. Otvor `report/report_level2.tex`.
4. Pri uložení súboru sa spustí build cez Docker.
5. V LaTeX Workshop otvor PDF náhľad a presuň ho do vedľajšieho editora, aby si mal `.tex` a PDF side by side.

Čistenie pomocných súborov je nastavené cez `latex-workshop.latex.clean.method = glob`, takže LaTeX Workshop ich maže priamo vo workspace a nepoužíva lokálny MiKTeX `latexmk` ani Perl.

SyncTeX navigácia je tiež zapnutá. Po builde otvor PDF cez interný LaTeX Workshop viewer a dvojklikom na miesto v PDF skočíš na zodpovedajúce miesto v `.tex`. Opačný smer funguje cez príkaz `LaTeX Workshop: SyncTeX from cursor`.

Keďže LaTeX beží v Docker kontajneri, pôvodný SyncTeX súbor by obsahoval cestu `/work/...`, ktorú Windows IDE nevie otvoriť. Workspace nastavenie preto po kompilácii spúšťa druhý Docker krok `synctex-path-fix-docker`, ktorý prepíše SyncTeX mapovanie na hostiteľskú cestu typu `C:/...`.

## 9. Užitočné kontroly

Zoznam služieb:

```powershell
docker compose --profile tools --profile ollama config --services
```

Stav kontajnerov:

```powershell
docker compose --profile tools --profile ollama ps
```

Logy web dema:

```powershell
docker compose --profile tools logs web-demo
```

Logy Ollamy:

```powershell
docker compose --profile ollama logs ollama
```

## 10. Vypnutie

Vypnutie web dema, app kontajnerov a Ollamy:

```powershell
docker compose --profile tools --profile ollama down
```

Zastavenie web demo služby:

```powershell
docker compose --profile tools stop web-demo
```

## 11. Vymazanie Docker artefaktov

Vymazanie kontajnerov a compose siete:

```powershell
docker compose --profile tools --profile ollama down
```

Vymazanie aj Ollama volume s modelmi:

```powershell
docker compose --profile tools --profile ollama down --volumes
```

Vymazanie image projektu:

```powershell
docker image rm eu-ai-gdpr-agent:latest
```

Voliteľné vymazanie vygenerovaných lokálnych artefaktov cez krátky Docker kontajner:

```powershell
docker run --rm -v "${PWD}:/work" -w /work alpine:3.20 sh -c "rm -rf outputs/* data/raw data/eval data/processed data/official"
```

Použi posledný príkaz iba vtedy, keď chceš odstrániť lokálne vygenerované dáta a výstupy. Zdrojový kód, reporty a konfigurácia zostanú zachované.

## 12. Štruktúra projektu

```text
akt3/
|-- .dockerignore                         # Súbory vylúčené z Docker build contextu.
|-- aktivitkac3.pdf                       # Pôvodné zadanie v PDF.
|-- Dockerfile                            # Image pre Python aplikáciu a evaluáciu.
|-- docker-compose.yml                    # Služby app, quick, test, demo, web-demo a ollama.
|-- Makefile                              # Pomocné aliasy; README však používa Docker príkazy priamo.
|-- README.md                             # Tento návod.
|-- requirements.txt                      # Python závislosti inštalované v Docker image.
|-- run_eval.py                           # Hlavný spúšťač benchmarku S0/S1/S2, ablations, Pareto a red-team.
|
|-- configs/
|   `-- level2.yaml                       # Konfigurácia modelov, retrievalu, agenta, ciest a evaluácie.
|
|-- data/
|   |-- sources.json                      # Spoločný manifest seed + official dokumentov.
|   |
|   |-- eval/
|   |   `-- questions.json                # Testovacia sada 36 otázok s expected_answer, citáciami, red-team a abstain prípadmi.
|   |
|   |-- processed/
|   |   `-- chunks.json                   # Vygenerovaný RAG index chunkov s doc_id/chunk_id/citáciami.
|   |
|   |-- official/
|   |   |-- fetch_metadata.json           # Metadata stiahnutia oficiálnych zdrojov, hash a word counts.
|   |   |-- sources.json                  # Manifest 7 oficiálnych snapshotov.
|   |   `-- raw/
|   |       |-- official_ai_act_full.md                         # Plný snapshot EU AI Act z EUR-Lex.
|   |       |-- official_gdpr_full.md                           # Plný snapshot GDPR z EUR-Lex.
|   |       |-- official_ec_data_protection_rules.md             # European Commission data protection rules.
|   |       |-- official_ec_gdpr_principles.md                   # European Commission GDPR principles.
|   |       |-- official_edpb_consent_guidelines.md              # EDPB Guidelines 05/2020 on consent.
|   |       |-- official_edpb_wp29_endorsed_guidelines.md        # EDPB endorsed WP29 guidance overview.
|   |       `-- official_gpai_code_practice.md                  # European Commission GPAI Code page.
|   |
|   `-- raw/
|       |-- ai_act_ai_literacy.html       # Seed dokument k AI literacy povinnosti.
|       |-- ai_act_deployer_obligations.md # Seed dokument k deployer povinnostiam.
|       |-- ai_act_gpai.html              # Seed dokument k GPAI povinnostiam.
|       |-- ai_act_high_risk.md           # Seed dokument k high-risk klasifikácii.
|       |-- ai_act_overview.html          # Seed overview EU AI Act.
|       |-- ai_act_penalties.pdf          # Seed dokument k AI Act pokutám.
|       |-- ai_act_prohibited.pdf         # Seed dokument k zakázaným AI praktikám.
|       |-- ai_act_provider_obligations.html # Seed dokument k provider povinnostiam.
|       |-- ai_act_timeline.md            # Seed dokument k časovej osi AI Act.
|       |-- ai_act_transparency.pdf       # Seed dokument k transparentnosti chatbotov.
|       |-- case_credit_scoring.html      # Case note: AI kreditné skórovanie.
|       |-- case_customer_chatbot.pdf     # Case note: customer support chatbot.
|       |-- case_health_triage.md         # Case note: health triage AI.
|       |-- case_hr_screening.md          # Case note: ranking CV kandidátov.
|       |-- distractor_ai_act_no_high_risk.html # Distraktor s nesprávnym tvrdením o high-risk.
|       |-- distractor_ai_act_public_only.pdf   # Distraktor s tvrdením, že AI Act platí len pre verejný sektor.
|       |-- distractor_gdpr_consent_everything.md # Distraktor s chybným tvrdením o consente.
|       |-- duplicate_ai_act_overview_a.md # Skoro duplicitný AI Act overview.
|       |-- duplicate_gdpr_principles_a.html # Skoro duplicitný GDPR principles dokument.
|       |-- edpb_automated_guidelines.md  # Seed dokument k automated decision-making.
|       |-- edpb_consent.md               # Seed dokument k EDPB consent pravidlám.
|       |-- edpb_dpia_guidelines.html     # Seed dokument k DPIA guidelines.
|       |-- edpb_transparency_guidelines.pdf # Seed dokument k transparentnosti.
|       |-- gdpr_automated_decision.html  # Seed dokument k GDPR Article 22.
|       |-- gdpr_dpia.pdf                 # Seed dokument k DPIA.
|       |-- gdpr_dpo.html                 # Seed dokument k DPO.
|       |-- gdpr_lawful_basis.pdf         # Seed dokument k lawful bases.
|       |-- gdpr_overview.html            # Seed overview GDPR.
|       |-- gdpr_principles.md            # Seed dokument k GDPR Article 5 princípom.
|       |-- gdpr_rights.html              # Seed dokument k právam dotknutej osoby.
|       |-- gdpr_security_breach.pdf      # Seed dokument k bezpečnosti a breach notification.
|       |-- gdpr_special_categories.md    # Seed dokument k special category data.
|       |-- gdpr_transparency.md          # Seed dokument k GDPR transparentnosti.
|       |-- injection_vendor_policy.html  # Prompt-injection dokument pre red-team.
|       `-- secret_board_minutes.md       # Falošný secret dokument s access flagom pre leak testy.
|
|
|-- outputs/
|   |-- ablation_quality.svg             # Graf kvality ablačných variantov.
|   |-- pareto_quality_cost.svg          # Pareto graf cena/kvalita.
|   |-- metrics.csv                      # Detailné metriky po otázkach a variantoch.
|   |-- metrics.json                     # Detailné metriky v JSON forme.
|   |-- summary.json                     # Agregované výsledky variantov, ablations, Pareto a red-team.
|   |-- report_summary.md                # Markdown sumarizácia evaluácie.
|   |-- trace_samples.json               # Ukážkové trace logy vybraných behov.
|   |-- live_demo_server.log             # Voliteľný log lokálneho web dema.
|   |-- live_demo_server.err.log         # Voliteľný error log lokálneho web dema.
|   `-- traces/
|       |-- q01_124cca52.json            # Trace log demo/eval behu otázky q01.
|       |-- q02_7858feeb.json            # Trace log demo/eval behu otázky q02.
|       |-- q03_653ff651.json            # Trace log demo/eval behu otázky q03.
|       |-- q04_0cfb4507.json            # Trace log demo/eval behu otázky q04.
|       `-- q05_b0177aed.json            # Trace log demo/eval behu otázky q05.
|
|-- rag_agent/
|   |-- __init__.py                      # Package marker a základné metadata balíka.
|   |-- agents.py                        # Multi-agent tok: InputGuard, Planner, Executor, Critic, OutputGuard.
|   |-- demo.py                          # CLI demo pre jednu alebo predpripravené otázky.
|   |-- documents.py                     # Materializácia dokumentov, parsery a chunkovanie.
|   |-- evaluator.py                     # Evaluácia variantov, ablations, red-team a sumarizácia metrík.
|   |-- guardrails.py                    # Input/output guardrails pre PII, off-topic, secret a prompt injection.
|   |-- llm.py                           # Ollama klient a deterministický fallback.
|   |-- official_sources.py              # Allowlistovaný downloader oficiálnych zdrojov.
|   |-- retrieval.py                     # BM25, dense embeddings, RRF, reranking a citation verifier.
|   |-- schemas.py                       # Typy AnswerSchema, Citation, TypedPlan, RunTrace a JudgeScore.
|   |-- seed_data.py                     # Seed dokumenty a testovacia sada generovaná do data/.
|   |-- tools.py                         # Nástroje search_kb, calculator, run_python a eurlex_fetch.
|   `-- web_demo.py                      # HTTP server pre živú demo stránku.
|
|-- report/
|   |-- live_demo.html                   # Frontend pre živé demo s vlastnou otázkou.
|   |-- report.md                        # Markdown verzia reportu.
|   |-- report_level2.tex                # LaTeX report v KOMA-Script šablóne.
|   |-- report_level2.pdf                # Vygenerovaný PDF report.
|
|-- scripts/
|   |-- materialize_corpus.py            # Docker-spúšťateľný skript na vygenerovanie seed corpus dát.
|   `-- update_official_sources.py       # Docker-spúšťateľný skript na aktualizáciu oficiálnych snapshotov.
|
`-- tests/
    `-- test_core.py                     # Unit/integration testy pre schema, retrieval, guardrails a agenta.
```

Poznámka: `data/` a `outputs/` obsahujú vygenerované alebo overovacie artefakty. Sú ponechané v repozitári, aby bolo možné zadanie skontrolovať aj bez opätovného dlhého behu.

## 13. Zdroje

Oficiálne snapshoty a seed dokumenty vychádzajú z týchto zdrojov:

- EU AI Act: https://eur-lex.europa.eu/eli/reg/2024/1689/oj/eng
- GDPR: https://eur-lex.europa.eu/legal-content/EN/TXT/HTML/?uri=CELEX:32016R0679
- European Commission data protection: https://commission.europa.eu/law/law-topic/data-protection/eu-data-protection-rules_en
- European Commission GDPR principles: https://commission.europa.eu/law/law-topic/data-protection/rules-business-and-organisations/principles-gdpr_en
- EDPB consent guidelines: https://www.edpb.europa.eu/our-work-tools/our-documents/guidelines/guidelines-052020-consent-under-regulation-2016679_en
- EDPB endorsed WP29 guidelines: https://www.edpb.europa.eu/our-work-tools/general-guidance/endorsed-wp29-guidelines_en
- GPAI Code of Practice page: https://digital-strategy.ec.europa.eu/en/policies/contents-code-gpai

## 14. Obmedzenia

Toto je vzdelávací compliance asistent, nie právne poradenstvo. Systém je vhodný na interný triage a obhajobu zadania, ale produkčné nasadenie by vyžadovalo pravidelnú aktualizáciu zdrojov, audit parsera, monitoring kvality a právnu kontrolu odpovedí s vysokým dopadom.
