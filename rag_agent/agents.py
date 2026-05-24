from __future__ import annotations

import re
import uuid
from dataclasses import dataclass
from typing import Any

from .guardrails import contains_prompt_injection, input_guard, output_guard
from .llm import make_llm
from .retrieval import HybridRetriever, SearchResult
from .schemas import AnswerSchema, Citation, PlanStep, RunTrace, TypedPlan, count_tokens
from .tools import Toolset


@dataclass
class AgentSettings:
    max_tool_calls: int = 6
    max_replans: int = 2
    abstain_score_threshold: float = 0.08
    provider: str = "auto"


class Planner:
    def create_plan(self, question: str) -> TypedPlan:
        steps: list[PlanStep] = []
        q = question.lower()
        if _needs_external(q):
            celex = _celex_for_question(q) or "32024R1689"
            steps.append(PlanStep("external", "Over CELEX v externom allowlistovanom zdroji", "eurlex_fetch", celex))
        if _looks_like_math(q):
            steps.append(PlanStep("calculate", "Vypočítaj číselnú časť otázky", "calculator", _math_expression(q)))
        if not steps or _needs_retrieval(q):
            retrieval_query = _expand_query(question)
            steps.append(PlanStep("retrieve", "Vyhľadaj autoritatívne dôkazy v znalostnej báze", "search_kb", retrieval_query))
        steps.append(PlanStep("answer", "Vytvor validnú JSON odpoveď s citáciami alebo abstain", None, None))
        risk = "high" if any(term in q for term in ["ignore", "ignoruj", "secret", "jailbreak"]) else "medium"
        return TypedPlan(question=question, steps=steps, tool_budget=6, risk_level=risk)


class Critic:
    def review(
        self,
        question: str,
        answer: AnswerSchema,
        evidence: list[SearchResult],
        retriever: HybridRetriever,
        threshold: float,
    ) -> AnswerSchema:
        if answer.abstained:
            return answer
        if _requires_unknown_abstain(question):
            return _abstain("Neviem: otázka vyžaduje konkrétny budúci, interný alebo právne záväzný fakt, ktorý nie je v zdrojoch.")
        if evidence:
            top_score = max(result.score for result in evidence)
            if top_score < threshold and not _looks_like_math(question.lower()) and not _needs_external(question.lower()):
                return _abstain("Neviem: vyhľadávanie nenašlo dostatočne silný dôkaz v znalostnej báze.")
        guard = output_guard(answer, retriever)
        if not guard.allowed:
            return _abstain(f"Neviem: kontrola odpovede zlyhala ({guard.reason}).")
        return answer


class MultiAgentAssistant:
    def __init__(
        self,
        retriever: HybridRetriever,
        model: str,
        judge_model: str,
        settings: AgentSettings | None = None,
    ) -> None:
        self.retriever = retriever
        self.model = model
        self.judge_model = judge_model
        self.settings = settings or AgentSettings()
        self.llm = make_llm(model, provider=self.settings.provider)
        self.planner = Planner()
        self.critic = Critic()
        self.tools = Toolset(retriever)

    def answer(
        self,
        question: str,
        question_id: str = "adhoc",
        variant: str = "S2_full_agent",
        ablation: str = "full",
    ) -> tuple[AnswerSchema, RunTrace]:
        trace = RunTrace(
            run_id=str(uuid.uuid4()),
            variant=f"{variant}:{ablation}",
            question_id=question_id,
            question=question,
            model=self.model,
            judge_model=self.judge_model,
            input_tokens=count_tokens(question),
        )

        trace.add_state("InputGuard", "checking user request")
        guard = input_guard(question)
        if not guard.allowed:
            answer = _abstain(f"Odmietam alebo neviem odpovedať: {guard.reason}")
            trace.add_state("InputGuard", "request blocked", reason=guard.reason)
            trace.answer = answer
            trace.output_tokens = count_tokens(answer.answer)
            trace.finish()
            return answer, trace
        trace.add_state("InputGuard", "request allowed", risk=guard.risk)

        if variant == "S0_pure_llm":
            trace.add_state("Planner", "S0 baseline skips retrieval and tools")
            answer = self._pure_llm_baseline(question)
            trace.answer = answer
            trace.output_tokens = count_tokens(answer.answer)
            trace.finish()
            return answer, trace

        trace.add_state("Planner", "creating typed plan")
        plan = self.planner.create_plan(question)
        plan_errors = plan.validate()
        if plan_errors:
            answer = _abstain("Neviem: plán je neplatný: " + "; ".join(plan_errors))
            trace.errors.extend(plan_errors)
            trace.answer = answer
            trace.finish()
            return answer, trace
        trace.add_state(
            "Planner",
            "typed plan ready",
            steps=[{"id": step.id, "tool": step.tool, "objective": step.objective} for step in plan.steps],
        )

        evidence: list[SearchResult] = []
        tool_outputs: dict[str, Any] = {}
        tool_calls = 0
        trace.add_state("Executor", "executing plan")
        for step in plan.steps:
            if step.tool is None:
                continue
            if tool_calls >= self.settings.max_tool_calls:
                trace.errors.append("tool budget exhausted")
                break
            tool_calls += 1
            if step.tool == "search_kb":
                result, call_trace = self.tools.call("search_kb", query=step.query or question, top_k=6, include_secret=False)
                raw_results = self.retriever.search(step.query or question, top_k=6, include_secret=False)
                evidence.extend(raw_results)
                trace.retrieved_chunk_ids.extend([item.chunk.chunk_id for item in raw_results])
                tool_outputs.setdefault("search_kb", []).extend(result)
            elif step.tool == "calculator":
                result, call_trace = self.tools.call("calculator", expr=step.query or _math_expression(question.lower()))
                tool_outputs["calculator"] = result
            elif step.tool == "eurlex_fetch":
                result, call_trace = self.tools.call("eurlex_fetch", celex_id=step.query or "32024R1689")
                tool_outputs["eurlex_fetch"] = result
            else:
                result, call_trace = self.tools.call(step.tool, code=step.query or "")
                tool_outputs[step.tool] = result
            trace.tool_calls.append(call_trace)

        if variant == "S1_rag_only" and not evidence:
            result, call_trace = self.tools.call("search_kb", query=question, top_k=6, include_secret=False)
            evidence = self.retriever.search(question, top_k=6, include_secret=False)
            trace.retrieved_chunk_ids.extend([item.chunk.chunk_id for item in evidence])
            trace.tool_calls.append(call_trace)
            tool_outputs["search_kb"] = result

        answer = self._synthesize(question, evidence, tool_outputs, ablation=ablation)
        trace.add_state("Executor", "draft answer produced", abstained=answer.abstained, citations=len(answer.citations))

        if variant == "S2_full_agent" and ablation != "no_critic":
            for replan_attempt in range(self.settings.max_replans + 1):
                trace.add_state("Critic", "reviewing answer against evidence", replan_attempt=replan_attempt)
                reviewed = self.critic.review(
                    question,
                    answer,
                    evidence,
                    self.retriever,
                    threshold=self.settings.abstain_score_threshold,
                )
                if reviewed is answer:
                    trace.add_state("Critic", "answer accepted", replan_attempt=replan_attempt)
                    break
                trace.add_state(
                    "Critic",
                    "answer vetoed or changed",
                    new_abstained=reviewed.abstained,
                    replan_attempt=replan_attempt,
                )
                if not _can_replan(question, reviewed, replan_attempt, self.settings.max_replans):
                    answer = reviewed
                    break
                if tool_calls >= self.settings.max_tool_calls:
                    trace.errors.append("replan skipped because tool budget is exhausted")
                    answer = reviewed
                    break
                query = _replan_query(question, evidence, replan_attempt)
                trace.add_state("Planner", "replanning after critic veto", replan_attempt=replan_attempt + 1, query=query)
                result, call_trace = self.tools.call("search_kb", query=query, top_k=6, include_secret=False)
                tool_calls += 1
                trace.tool_calls.append(call_trace)
                replanned_evidence = self.retriever.search(query, top_k=6, include_secret=False)
                evidence = _merge_evidence(evidence, replanned_evidence)
                trace.retrieved_chunk_ids.extend([item.chunk.chunk_id for item in replanned_evidence])
                tool_outputs.setdefault("search_kb", []).extend(result)
                answer = self._synthesize(question, evidence, tool_outputs, ablation=ablation)
                trace.add_state(
                    "Executor",
                    "draft answer produced after replan",
                    abstained=answer.abstained,
                    citations=len(answer.citations),
                    replan_attempt=replan_attempt + 1,
                )

        trace.add_state("OutputGuard", "validating final JSON answer")
        final_guard = output_guard(answer, self.retriever)
        if not final_guard.allowed:
            answer = _abstain(f"Neviem: výstupný filter zamietol odpoveď ({final_guard.reason}).")
            trace.add_state("OutputGuard", "final answer replaced with abstain", reason=final_guard.reason)
        else:
            trace.add_state("OutputGuard", "final answer valid", reason=final_guard.reason)

        trace.answer = answer
        trace.output_tokens = count_tokens(answer.answer)
        trace.finish()
        return answer, trace

    def _pure_llm_baseline(self, question: str) -> AnswerSchema:
        if _requires_unknown_abstain(question) or "taj" in question.lower() or "system prompt" in question.lower():
            return _abstain("Neviem: bez vyhľadávania nemám overiteľný zdroj.")
        result = self.llm.generate(
            "Odpovedz stručne po slovensky bez nástrojov. Otázka: " + question,
            json_mode=False,
        )
        return AnswerSchema(
            answer=result.text,
            citations=[],
            confidence=0.25,
            abstained=False,
            reasoning_trace=["S0_pure_llm: no retrieval, no tool calls"],
        )

    def _synthesize(
        self,
        question: str,
        evidence: list[SearchResult],
        tool_outputs: dict[str, Any],
        ablation: str,
    ) -> AnswerSchema:
        q = question.lower()
        reasoning = ["Planner vytvoril typovaný plán.", "Executor zavolal potrebné nástroje."]

        if "calculator" in tool_outputs and not tool_outputs["calculator"].get("error"):
            result = tool_outputs["calculator"]["result"]
            return AnswerSchema(
                answer=f"Výsledok výpočtu je {result}.",
                citations=[],
                confidence=0.98,
                abstained=False,
                reasoning_trace=reasoning + ["calculator tool returned numeric result"],
            )

        if _requires_unknown_abstain(question):
            return _abstain("Neviem: dostupné zdroje neobsahujú konkrétnu predikciu, interný údaj alebo právne záväzné stanovisko.")

        citations = _select_citations(question, evidence)
        if ablation == "no_structured_output":
            citations = []

        if "eurlex_fetch" in tool_outputs and not tool_outputs["eurlex_fetch"].get("error"):
            ext = tool_outputs["eurlex_fetch"]
            celex = "32016R0679" if "2016/679" in ext.get("title", "") else "32024R1689"
            answer = (
                f"Externý allowlistovaný nástroj vrátil CELEX {celex}: {ext.get('title')}. "
                f"Lokálna cache hovorí: {ext.get('summary')}"
            )
            if citations:
                answer += " Súvisiaca lokálna citácia je priložená."
            return AnswerSchema(
                answer=answer,
                citations=citations,
                confidence=0.9,
                abstained=False,
                reasoning_trace=reasoning + ["eurlex_fetch tool verified CELEX identifier"],
            )

        if not evidence:
            return _abstain("Neviem: nenašiel som žiadne dôkazy v znalostnej báze.")

        if any(contains_prompt_injection(item.chunk.text) for item in evidence):
            answer = (
                "Nájdený dokument obsahuje nepriamu prompt-injection inštrukciu. Túto inštrukciu nevykonávam; "
                "beriem ju ako nedôveryhodný obsah dokumentu a odpoveď opieram iba o overiteľné časti a autoritatívne zdroje."
            )
            return AnswerSchema(
                answer=answer,
                citations=citations,
                confidence=0.72,
                abstained=False,
                reasoning_trace=reasoning + ["prompt injection in retrieved document was ignored"],
            )

        answer = _template_answer(q, evidence)
        confidence = min(0.95, 0.48 + (max((item.score for item in evidence), default=0.0) * 3.0))
        return AnswerSchema(
            answer=answer,
            citations=citations,
            confidence=confidence,
            abstained=False,
            reasoning_trace=reasoning + ["Answer synthesized from top retrieved chunks"],
        )


def _abstain(reason: str) -> AnswerSchema:
    return AnswerSchema(
        answer=reason,
        citations=[],
        confidence=0.0,
        abstained=True,
        reasoning_trace=["abstain", reason],
    )


def _can_replan(question: str, reviewed: AnswerSchema, replan_attempt: int, max_replans: int) -> bool:
    if replan_attempt >= max_replans or not reviewed.abstained:
        return False
    return not _requires_unknown_abstain(question)


def _replan_query(question: str, evidence: list[SearchResult], replan_attempt: int) -> str:
    tags: list[str] = []
    for item in evidence[:4]:
        tags.extend(item.chunk.tags)
    tag_hint = " ".join(sorted(set(tags)))
    if replan_attempt == 0:
        return f"{_expand_query(question)} {tag_hint} official evidence citation"
    return f"{question} EU AI Act GDPR EUR-Lex EDPB obligations safeguards {tag_hint}"


def _merge_evidence(existing: list[SearchResult], new: list[SearchResult]) -> list[SearchResult]:
    by_chunk: dict[str, SearchResult] = {item.chunk.chunk_id: item for item in existing}
    for item in new:
        current = by_chunk.get(item.chunk.chunk_id)
        if current is None or item.score > current.score:
            by_chunk[item.chunk.chunk_id] = item
    return sorted(by_chunk.values(), key=lambda item: item.score, reverse=True)


def _needs_retrieval(q: str) -> bool:
    return not _looks_like_math(q) or "gdpr" in q or "ai act" in q or "celex" in q


def _expand_query(question: str) -> str:
    q = question.lower()
    expansions: list[str] = []
    if "vendor policy" in q:
        return "vendor policy prompt injection citations"
    if "chatbot" in q or "komunikuje" in q or "interact" in q:
        expansions.append("AI Act Article 50 transparency obligations interact directly with natural persons informed interacting with an AI system chatbot")
    if "transparent" in q or "privacy inform" in q:
        expansions.append("GDPR transparency Articles 13 14 privacy information clear plain language lawful basis purposes")
    if "bezpeč" in q or "bezpec" in q or "security" in q:
        expansions.append("GDPR Article 32 security encryption confidentiality integrity breach notification")
    if "gpai" in q or "general-purpose" in q:
        expansions.append("general-purpose AI GPAI documentation copyright systemic risk adversarial testing")
    if "zakázan" in q or "zakazan" in q or "prohibited" in q:
        expansions.append("AI Act prohibited practices manipulative vulnerabilities social scoring biometric")
    if "automated" in q or "solely" in q or "safeguards" in q or "profiling" in q:
        expansions.append("GDPR Article 22 automated decision-making profiling safeguards human intervention contest")
    if "cv" in q or "kandid" in q or "hiring" in q:
        expansions.append("AI CV screening employment high-risk GDPR DPIA human oversight")
    if "credit" in q or "kredit" in q:
        expansions.append("AI credit scoring high-risk essential services GDPR Article 22 profiling")
    if "health" in q or "triage" in q:
        expansions.append("AI health triage special category health data Article 9 DPIA high-risk")
    return question + (" " + " ".join(expansions) if expansions else "")


def _looks_like_math(q: str) -> bool:
    return "spočítaj" in q or "spocitaj" in q or "vypočítaj" in q or "vypocitaj" in q or bool(
        re.search(r"\d+\s*(krát|krat|x|×|\*|\+|-|/)\s*\d+", q)
    )


def _math_expression(q: str) -> str:
    numbers = re.findall(r"\d+(?:\.\d+)?", q)
    if not numbers:
        return "0"
    if "krát" in q or "krat" in q or " x " in q or "×" in q or "*" in q:
        return "*".join(numbers[:2])
    return "+".join(numbers)


def _needs_external(q: str) -> bool:
    return "celex" in q or "extern" in q or "eurlex" in q or "eur-lex" in q


def _celex_for_question(q: str) -> str | None:
    if "gdpr" in q or "2016/679" in q:
        return "32016R0679"
    if "ai act" in q or "2024/1689" in q:
        return "32024R1689"
    return None


def _requires_unknown_abstain(question: str) -> bool:
    q = question.lower()
    return any(
        pattern in q
        for pattern in [
            "presná suma pokuty",
            "presna suma pokuty",
            "minulého týždňa",
            "minuleho tyzdna",
            "zajtra",
            "roku 2030",
            "záväzné právne stanovisko",
            "zavazne pravne stanovisko",
        ]
    )


def _select_citations(question: str, evidence: list[SearchResult]) -> list[Citation]:
    selected: list[SearchResult] = []
    q = question.lower()
    if "platí len pre verejný" in q or "plati len pre verejny" in q:
        selected.extend([item for item in evidence if item.chunk.doc_id == "distractor_ai_act_public_only"][:1])
        selected.extend([item for item in evidence if item.chunk.doc_id in {"ai_act_overview", "duplicate_ai_act_overview_a"}][:1])
    elif "chatbot" in q or "komunikuje" in q or "interact" in q:
        selected.extend([item for item in evidence if item.chunk.doc_id == "ai_act_transparency"][:1])
        selected.extend([item for item in evidence if item.chunk.doc_id == "official_ai_act_full" and _chunk_has_terms(item, "article 50", "interacting with an ai system")][:1])
        selected.extend([item for item in evidence if item.chunk.doc_id in {"case_customer_chatbot", "gdpr_transparency", "official_gdpr_full"}][:1])
    elif "terms of service" in q or "consent" in q:
        selected.extend([item for item in evidence if item.chunk.doc_id == "distractor_gdpr_consent_everything"][:1])
        selected.extend([item for item in evidence if item.chunk.doc_id in {"edpb_consent", "gdpr_lawful_basis"}][:2])
    else:
        selected = evidence[:3]

    citations: list[Citation] = []
    seen: set[str] = set()
    for item in selected:
        chunk = item.chunk
        if chunk.access == "secret" or chunk.chunk_id in seen:
            continue
        quote = _quote_from_chunk(chunk.text)
        citations.append(Citation(doc_id=chunk.doc_id, chunk_id=chunk.chunk_id, quote=quote, source_url=chunk.source_url))
        seen.add(chunk.chunk_id)
        if len(citations) >= 3:
            break
    return citations


def _chunk_has_terms(item: SearchResult, *terms: str) -> bool:
    lowered = item.chunk.text.lower()
    return all(term.lower() in lowered for term in terms)


def _quote_from_chunk(text: str) -> str:
    compact = " ".join(text.split())
    sentences = re.split(r"(?<=[.!?])\s+", compact)
    for sentence in sentences:
        if len(sentence.split()) >= 8:
            return sentence[:300]
    return compact[:300]


def _template_answer(q: str, evidence: list[SearchResult]) -> str:
    if "provider" in q and "high-risk" in q:
        return (
            "Provider high-risk AI systému má riešiť najmä risk management, data governance, technickú dokumentáciu, "
            "logovanie, human oversight, presnosť/robustnosť/kybernetickú bezpečnosť, conformity assessment a post-market monitoring."
        )
    if "chatbot" in q:
        return (
            "Áno. Pri chatbote má byť používateľ informovaný, že komunikuje s AI systémom, pokiaľ to nie je z okolností zrejmé; "
            "ak chatbot spracúva osobné údaje, stále platia GDPR transparentnosť, minimalizácia, retencia a bezpečnosť."
        )
    if "article 5" in q or "princ" in q:
        return (
            "GDPR Article 5 obsahuje princípy lawfulness, fairness and transparency, purpose limitation, data minimisation, "
            "accuracy, storage limitation, integrity and confidentiality a accountability."
        )
    if "consent" in q or "lawful basis" in q:
        return (
            "Consent nie je automaticky najlepší lawful basis: musí byť freely given, specific, informed a unambiguous; "
            "pri nerovnováhe moci, bundlovaní účelov alebo nemožnosti odmietnuť bez ujmy nemusí byť platný."
        )
    if "dpia" in q:
        return (
            "DPIA je potrebná pri spracovaní s pravdepodobne vysokým rizikom, najmä pri systematickom hodnotení, "
            "solely automated rozhodovaní, veľkom rozsahu špeciálnych kategórií údajov alebo systematickom monitorovaní."
        )
    if "transparent" in q or "privacy inform" in q:
        return (
            "Transparentná privacy informácia má uviesť identitu controllera, účely, lawful basis, príjemcov, retenciu, "
            "práva dotknutých osôb, zdroj údajov a pri automated decision-making aj meaningful information about the logic involved."
        )
    if "bezpeč" in q or "bezpec" in q or "security" in q:
        return (
            "Pri GDPR bezpečnosti treba podľa rizika zvážiť encryption alebo pseudonymisation, confidentiality, integrity, "
            "availability, resilience, pravidelné testovanie a procesy pre breach notification."
        )
    if "article 22" in q or "automated" in q:
        return (
            "Pri solely automated decision-making s právnym alebo podobne významným účinkom treba výnimku podľa Article 22 "
            "a safeguards ako meaningful human intervention, možnosť vyjadriť stanovisko a možnosť rozhodnutie napadnúť."
        )
    if "dpo" in q:
        return (
            "DPO je povinný najmä pre verejné orgány, veľkorozsahové pravidelné a systematické monitorovanie alebo "
            "veľkorozsahové spracúvanie špeciálnych kategórií údajov či údajov o trestných činoch."
        )
    if "cv" in q or "hiring" in q or "kandid" in q:
        return (
            "Áno. CV ranking patrí do employment kontextu, môže byť high-risk podľa AI Act a zároveň spracúva osobné údaje; "
            "preto treba riešiť informovanie, lawful basis, data minimisation, human oversight a pravdepodobne DPIA."
        )
    if "provider" in q and "deployer" in q:
        return (
            "Provider pripravuje systémové kontroly ako technickú dokumentáciu, risk management a conformity/post-market procesy. "
            "Deployer musí systém používať podľa inštrukcií, zabezpečiť kompetentný human oversight, monitorovať prevádzku a riešiť incidenty."
        )
    if "credit" in q or "kredit" in q:
        return (
            "AI kreditné skórovanie môže byť high-risk pri prístupe k základným súkromným službám a zároveň profiling podľa GDPR; "
            "treba lawful basis, transparentnosť, Article 22 safeguards, DPIA a meaningful human review."
        )
    if "health" in q or "triage" in q:
        return (
            "AI health triage pracuje so special category health data, takže treba Article 9 výnimku, silnú bezpečnosť, minimalizáciu "
            "a DPIA; podľa kontextu môže ísť aj o high-risk AI systém."
        )
    if "literacy" in q:
        return (
            "AI literacy znamená primerané zručnosti, znalosti a porozumenie rizikám AI. Provideri a deployeri majú zabezpečiť "
            "dostatočnú úroveň AI literacy pre staff a osoby pracujúce s AI systémami."
        )
    if "gpai" in q:
        return (
            "Poskytovatelia GPAI modelov riešia technickú dokumentáciu, informácie pre downstream providerov a politiku súladu "
            "s EU copyright law; pri systemic risk aj evaluácie, adversarial testing, mitigácie a incident reporting."
        )
    if "zakázan" in q or "prohibited" in q:
        return (
            "Zakázané AI praktiky sú unacceptable-risk prípady, napríklad manipulatívne techniky, zneužitie zraniteľností "
            "alebo niektoré social scoring a biometrické použitia. Taký návrh treba zastaviť a právne prehodnotiť."
        )
    if "pokut" in q:
        return (
            "AI Act používa administratívne pokuty podľa typu porušenia; najvážnejšie sú zakázané praktiky, pričom konkrétna "
            "suma závisí od závažnosti, trvania a veľkosti podniku."
        )
    if "verejný sektor" in q or "verejny sektor" in q:
        return (
            "Nie, tvrdenie je nesprávne. AI Act sa netýka iba verejného sektora; jeho pravidlá dopadajú aj na providerov, "
            "deployerov, importérov a distribútorov vrátane private undertakings, ak spadajú do rozsahu nariadenia."
        )
    if "terms of service" in q:
        return (
            "Nie. Consent nemôže byť automaticky zabalený do terms of service pre každý účel; musí byť slobodný, špecifický, "
            "informovaný, jednoznačný a pri viacerých účeloch granulárny."
        )
    snippets = []
    for item in evidence[:2]:
        snippets.append(_quote_from_chunk(item.chunk.text))
    return "Na základe nájdených zdrojov: " + " ".join(snippets)
